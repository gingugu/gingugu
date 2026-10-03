#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# ///

"""
PreToolUse Safety Gate

Blocks writes to sensitive files (.env, credentials, secrets, .pem, .key)
and prevents dangerous rm commands.
"""

import json
import os
import re
import sys


def append_to_log(log_name: str, input_data: dict) -> None:
    """Append input data to logs/<log_name>.json"""
    try:
        log_dir = os.path.join(os.getcwd(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"{log_name}.json")

        if os.path.exists(log_path):
            with open(log_path, "r") as f:
                try:
                    log_data = json.load(f)
                except (json.JSONDecodeError, ValueError):
                    log_data = []
        else:
            log_data = []

        log_data.append(input_data)

        with open(log_path, "w") as f:
            json.dump(log_data, f, indent=2)
    except Exception:
        pass  # Silent failure


def is_dangerous_rm_command(command):
    """Block only *catastrophic* recursive deletes, not every ``rm -rf``.

    A recursive rm is dangerous when its target is the filesystem root, the
    home dir, the parent/current dir, or a bare wildcard. Named targets like
    ``rm -rf dist`` / ``rm -rf build node_modules`` are ordinary cleanup and
    pass through. Non-recursive ``rm`` is never blocked here.
    """
    # Exact operands that must never be recursively deleted.
    catastrophic = {
        "/", "/*",
        "~", "~/", "~/*",
        "$HOME", "$HOME/", "$HOME/*", "${HOME}", "${HOME}/*",
        "..", "../", "../*",
        ".", "./", "./*",
        "*",
    }

    # Judge each command segment on its own: a `cd ~` elsewhere in the line
    # is not an operand of the rm.
    for segment in re.split(r"&&|\|\||[;|\n]", command):
        tokens = segment.split()
        while tokens and tokens[0] in ("sudo", "command", "exec", "nohup"):
            tokens = tokens[1:]
        if not tokens or not (tokens[0] == "rm" or tokens[0].endswith("/rm")):
            continue
        flags = [t for t in tokens[1:] if t.startswith("-")]
        recursive = any(f == "--recursive" or (not f.startswith("--") and re.search(r"[rR]", f)) for f in flags)
        if not recursive:
            continue
        if any(t in catastrophic for t in tokens[1:] if not t.startswith("-")):
            return True

    return False


def names_private_ssh_key(text):
    """True if ``text`` names an SSH private key (``id_rsa``, ``id_ed25519_work``...).

    The ``.pub`` half of the pair is public by design - ``ssh-copy-id -i
    ~/.ssh/id_ed25519.pub`` must pass - so only names without it count.
    """
    for name in re.findall(r"id_(?:rsa|ed25519|ecdsa|dsa)[\w.-]*", text):
        if not name.endswith(".pub"):
            return True
    return False


def is_sensitive_file_access(tool_name, tool_input):
    """
    Check if any tool is trying to access sensitive files.
    Blocks: .env, credentials, secrets, .pem, .key files.
    """
    sensitive_patterns = [
        # A dotenv file is one whose NAME starts with .env (.env, .env.local):
        # .env not glued to a letter, digit or dot. `gingugu.env` is a systemd
        # EnvironmentFile, not a dotenv file; `\.env`, `{.env,}` and `C:\x\.env`
        # are still dotenv files.
        r"(?<![\w.])\.env\b(?!\.sample|\.example|\.template)",
        r"credentials\.(json|yaml|yml|xml|toml)",
        r"secrets?\.(json|yaml|yml|xml|toml)",
        r"\.pem$",
        r"\.key$",
    ]

    if tool_name in ["Read", "Edit", "MultiEdit", "Write"]:
        file_path = tool_input.get("file_path", "")
        if names_private_ssh_key(file_path):
            return True
        for pattern in sensitive_patterns:
            if re.search(pattern, file_path, re.IGNORECASE):
                return True

    elif tool_name == "Bash":
        command = tool_input.get("command", "")
        if names_private_ssh_key(command) and not re.match(r"^(ls|find|git)\s", command.strip()):
            return True
        for pattern in sensitive_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                if re.match(r"^(ls|find|git)\s", command.strip()):
                    return False
                return True

    return False


def main():
    try:
        input_data = json.load(sys.stdin)

        append_to_log("pre_tool_use", input_data)

        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})

        if is_sensitive_file_access(tool_name, tool_input):
            msg = (
                "BLOCKED: Access to sensitive files "
                "(.env, credentials, secrets, keys) "
                "is prohibited"
            )
            print(msg, file=sys.stderr)
            print(
                "Use .env.sample or .env.example for "
                "template files instead",
                file=sys.stderr,
            )
            sys.exit(2)

        if tool_name == "Bash":
            command = tool_input.get("command", "")
            if is_dangerous_rm_command(command):
                print(
                    "BLOCKED: Dangerous rm command "
                    "detected and prevented",
                    file=sys.stderr,
                )
                sys.exit(2)

        sys.exit(0)

    except json.JSONDecodeError:
        sys.exit(0)
    except Exception:
        sys.exit(0)


if __name__ == "__main__":
    main()
