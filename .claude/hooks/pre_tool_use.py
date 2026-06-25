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
    """
    Comprehensive detection of dangerous rm commands.
    Matches various forms of rm -rf and similar destructive patterns.
    """
    normalized = " ".join(command.lower().split())

    patterns = [
        r"\brm\s+.*-[a-z]*r[a-z]*f",
        r"\brm\s+.*-[a-z]*f[a-z]*r",
        r"\brm\s+--recursive\s+--force",
        r"\brm\s+--force\s+--recursive",
        r"\brm\s+-r\s+.*-f",
        r"\brm\s+-f\s+.*-r",
    ]

    for pattern in patterns:
        if re.search(pattern, normalized):
            return True

    dangerous_paths = [
        r"/",
        r"/\*",
        r"~",
        r"~/",
        r"\$HOME",
        r"\.\.",
        r"\*",
        r"\.",
        r"\.\s*$",
    ]

    if re.search(r"\brm\s+.*-[a-z]*r", normalized):
        for path in dangerous_paths:
            if re.search(path, normalized):
                return True

    return False


def is_sensitive_file_access(tool_name, tool_input):
    """
    Check if any tool is trying to access sensitive files.
    Blocks: .env, credentials, secrets, .pem, .key files.
    """
    sensitive_patterns = [
        r"\.env\b(?!\.sample|\.example|\.template)",
        r"credentials\.(json|yaml|yml|xml|toml)",
        r"secrets?\.(json|yaml|yml|xml|toml)",
        r"\.pem$",
        r"\.key$",
        r"id_rsa",
        r"id_ed25519",
    ]

    if tool_name in ["Read", "Edit", "MultiEdit", "Write"]:
        file_path = tool_input.get("file_path", "")
        for pattern in sensitive_patterns:
            if re.search(pattern, file_path):
                return True

    elif tool_name == "Bash":
        command = tool_input.get("command", "")
        for pattern in sensitive_patterns:
            if re.search(pattern, command):
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
