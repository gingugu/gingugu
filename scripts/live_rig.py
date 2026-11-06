#!/usr/bin/env python3
"""A throwaway Claude Code project that runs THIS checkout's hooks and server.

Unit tests call `gingugu hook tool` directly, which is how two hook features
shipped dead: the installed doorway dropped the calls before the package ever
saw them. This rig runs the real chain - doorway, `gingugu hook tool`, MCP
server - from the checkout it lives in, on a scratch brain, so a hook change
can be seen working in Claude Code before it ships.

    scripts/live_rig.py                      # interactive, bypass mode: you click
    scripts/live_rig.py --headless "PROMPT"  # nobody to answer: prints denials
    scripts/live_rig.py --mode auto --dir /tmp/rig --credentials

Isolation is the point. On a machine in remote mode a scratch MEMORY_DB_PATH is
not enough (the machine setting ignores it on purpose), so every process gets
MEMORY_REMOTE_URL=off. The keychain is your real one: credentials are off
unless `--credentials`. Needs `uv sync --extra dev` in this checkout.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = Path("src/gingugu/bootstrap/templates/pre_tool_tripwire.py.tmpl")
MODES = ("bypassPermissions", "auto", "acceptEdits", "default", "plan", "dontAsk")


@dataclass
class Rig:
    root: Path
    db: Path
    mcp_config: Path
    env: dict = field(default_factory=dict)


def build(root: Path, repo: Path = REPO, *, credentials: bool = False) -> Rig:
    """Write the project: doorway, settings, MCP config. Returns how to run it."""
    root = Path(root).resolve()
    hooks = root / ".claude" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (root / "brain").mkdir(exist_ok=True)
    doorway = hooks / "pre_tool_tripwire.py"
    shutil.copyfile(repo / TEMPLATE, doorway)
    hook = {"type": "command", "command": f'python3 "{doorway}"', "timeout": 15}
    settings = {"hooks": {"PreToolUse": [{"matcher": "", "hooks": [hook]}]}}
    (root / ".claude" / "settings.json").write_text(json.dumps(settings, indent=2) + "\n")

    db = root / "brain" / "memories.db"
    scratch = {
        "MEMORY_DB_PATH": str(db),
        "MEMORY_NAMESPACE": "live",
        "MEMORY_REMOTE_URL": "off",  # NEVER the machine's remote brain
        "MEMORY_CREDENTIALS_ENABLED": "true" if credentials else "false",
    }
    bin_dir = repo / ".venv" / "bin"
    server = {"command": str(bin_dir / "gingugu"), "env": scratch}
    mcp_config = root / "mcp.json"
    mcp_config.write_text(json.dumps({"mcpServers": {"gingugu": server}}, indent=2) + "\n")
    # The doorway runs `gingugu hook tool` from PATH: this checkout's, first.
    env = {**os.environ, **scratch, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
    return Rig(root=root, db=db, mcp_config=mcp_config, env=env)


def command(rig: Rig, *, mode: str, prompt: str | None, model: str | None) -> list[str]:
    argv = ["claude", "--permission-mode", mode, "--setting-sources", "project"]
    argv += ["--strict-mcp-config", "--mcp-config", str(rig.mcp_config)]
    if model:
        argv += ["--model", model]
    if prompt is not None:
        argv += ["-p", prompt, "--output-format", "stream-json", "--verbose"]
    return argv


def _brain(db: Path) -> str:
    if not db.exists() or db.stat().st_size == 0:
        return (
            "scratch brain: no scratch brain written - if the run stored anything, "
            "it went somewhere else: NOT ISOLATED"
        )
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        count = conn.execute("SELECT count(*) FROM memories").fetchone()[0]
        conn.close()
    except sqlite3.Error as exc:
        return f"scratch brain: unreadable ({exc})"
    return f"scratch brain: {count} memories at {db}"


def summarize(lines: list[str], db: Path) -> list[str]:
    """The parts of a headless stream worth reading: mode, denials, results."""
    out = []
    for line in lines:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        kind, sub = msg.get("type"), msg.get("subtype")
        if kind == "system" and sub == "init":
            out.append(f"mode: {msg.get('permissionMode')}")
        elif kind == "system" and sub == "permission_denied":
            why = msg.get("decision_reason_type") or "rule"
            out.append(f"denied {msg.get('tool_name')} ({why}): {msg.get('decision_reason', '')}")
        elif kind == "user":
            for part in msg.get("message", {}).get("content", []):
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    flag = "error" if part.get("is_error") else "ok"
                    out.append(f"result ({flag}): {str(part.get('content'))[:160]}")
        elif kind == "result":
            out.append(f"end: {sub}, {len(msg.get('permission_denials') or [])} denial(s)")
    out.append(_brain(db))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dir", type=Path, help="project dir (default: a new temp dir)")
    parser.add_argument("--mode", choices=MODES, default="bypassPermissions")
    parser.add_argument("--headless", metavar="PROMPT", help="run `claude -p PROMPT`")
    parser.add_argument("--model", help="e.g. haiku for a cheap headless run")
    parser.add_argument("--credentials", action="store_true", help="vault on (REAL keychain)")
    args = parser.parse_args(argv)

    if not (REPO / ".venv" / "bin" / "gingugu").exists():
        print(f"no {REPO}/.venv/bin/gingugu - run `uv sync --extra dev` there", file=sys.stderr)
        return 2
    root = args.dir or Path(tempfile.mkdtemp(prefix="gingugu-live-"))
    rig = build(root, credentials=args.credentials)
    argv_ = command(rig, mode=args.mode, prompt=args.headless, model=args.model)
    print(f"live rig: {rig.root} (checkout {REPO}, mode {args.mode})", file=sys.stderr)
    if args.headless is None:
        return subprocess.call(argv_, cwd=rig.root, env=rig.env)
    done = subprocess.run(argv_, cwd=rig.root, env=rig.env, capture_output=True, text=True)
    (rig.root / "headless.jsonl").write_text(done.stdout + done.stderr)
    print("\n".join(summarize(done.stdout.splitlines(), rig.db)))
    return done.returncode


if __name__ == "__main__":
    sys.exit(main())
