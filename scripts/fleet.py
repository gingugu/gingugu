"""Upgrade gingugu on every machine you run it on, over SSH.

    uv run python scripts/fleet.py upgrade 0.24.0            # every machine
    uv run python scripts/fleet.py upgrade 0.24.0 pi laptop  # just these
    uv run python scripts/fleet.py status                    # version + brain, per machine

The machines live in ``~/.config/gingugu/fleet.json`` (``GINGUGU_FLEET``
overrides), never in this repo: host names are yours, and this repo is public.

    {"machines": [
      {"name": "laptop", "ssh": null, "uv": "uv"},
      {"name": "desk", "ssh": "me@desk.local", "uv": "/opt/homebrew/bin/uv"},
      {"name": "pi", "ssh": "me@brain.local", "uv": "$HOME/.local/bin/uv",
       "run_as": "gingugu", "with": ["keyrings.alt"], "service": "gingugu",
       "health": "https://127.0.0.1:8765/healthz"}
    ]}

``ssh: null`` is this machine. ``run_as`` installs as that user (``sudo -n``);
``service`` is restarted afterwards and ``health`` polled until it answers 200.
An install is retried for a few minutes: PyPI's index lags a fresh release.
``status`` shows each machine's version and which brain it talks to
(``gingugu remote status``), and exits 1 when one cannot reach it.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

_INSTALL_ATTEMPTS = 12
_INSTALL_WAIT = 20.0
_HEALTH_WAIT = 60.0


@dataclass
class Machine:
    name: str
    ssh: str | None = None
    uv: str = "uv"
    run_as: str | None = None
    with_: list[str] = field(default_factory=list)
    service: str | None = None
    health: str | None = None


class FleetError(Exception):
    """A machine could not be brought to the requested version."""


def config_path() -> Path:
    env = os.environ.get("GINGUGU_FLEET")
    return Path(env) if env else Path.home() / ".config" / "gingugu" / "fleet.json"


def load(path: Path) -> list[Machine]:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        raise FleetError(f"no fleet file at {path} (see this script's docstring)") from None
    machines = []
    for entry in data.get("machines", []):
        entry = dict(entry)
        entry["with_"] = list(entry.pop("with", []))
        machines.append(Machine(**entry))
    names = [m.name for m in machines]
    if len(set(names)) != len(names):
        raise FleetError(f"duplicate machine names in {path}")
    return machines


def as_user(machine: Machine, command: str) -> str:
    """``command`` as the install user, with ``~/.local/bin`` on PATH."""
    script = f'export PATH="$HOME/.local/bin:$PATH"; {command}'
    if machine.run_as:
        return f"sudo -n -u {shlex.quote(machine.run_as)} -H bash -c {shlex.quote(script)}"
    return f"bash -c {shlex.quote(script)}"


def install_command(machine: Machine, version: str) -> str:
    extras = "".join(f" --with {shlex.quote(dep)}" for dep in machine.with_)
    return as_user(
        machine, f"{machine.uv} tool install --force --refresh gingugu=={version}{extras}"
    )


def shell(machine: Machine, command: str, *, check: bool = True) -> str:
    argv = ["bash", "-c", command]
    if machine.ssh:
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", machine.ssh, command]
    done = subprocess.run(argv, text=True, capture_output=True)
    if check and done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()[-1:] or [""]
        raise FleetError(f"{machine.name}: `{command}` failed: {detail[0]}")
    return done.stdout.strip()


def installed(machine: Machine) -> str:
    out = shell(machine, as_user(machine, "gingugu --version"), check=False)
    return out.removeprefix("gingugu ").strip() or "not installed"


def parse_remote_status(text: str) -> dict[str, str]:
    """Parse `remote status` output into a dict with a ``mode`` key."""
    if text.strip() == "local":
        return {"mode": "local"}
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(": ")
        if sep:
            parsed[key.strip()] = value.strip()
    return {"mode": "remote", **parsed} if "remote" in parsed else {"mode": "unknown"}


def remote_status(machine: Machine) -> dict[str, str]:
    out = shell(machine, as_user(machine, "gingugu remote status"), check=False)
    return parse_remote_status(out)


def status_line(name: str, version: str, status: dict[str, str]) -> str:
    mode = status.get("mode")
    if mode == "local":
        return f"{name}: {version}, local"
    if mode == "remote":
        url = status["remote"]
        if status.get("reachable") == "yes":
            return f"{name}: {version}, remote {url}, reachable"
        hint = f" - {status['hint']}" if status.get("hint") else ""
        return f"{name}: {version}, remote {url}, UNREACHABLE{hint}"
    return f"{name}: {version}, remote status unknown"


def upgrade(machine: Machine, version: str) -> None:
    for attempt in range(1, _INSTALL_ATTEMPTS + 1):
        try:
            shell(machine, install_command(machine, version))
            break
        except FleetError as exc:
            if attempt == _INSTALL_ATTEMPTS:
                raise
            print(f"  {machine.name}: not installable yet ({exc}); retrying", flush=True)
            time.sleep(_INSTALL_WAIT)
    found = installed(machine)
    if found != version:
        raise FleetError(
            f"{machine.name}: installed {version} but `gingugu --version` says {found}"
        )
    if machine.service:
        restart(machine)


def restart(machine: Machine) -> None:
    shell(machine, f"sudo -n systemctl restart {shlex.quote(machine.service)}")
    if machine.health:
        probe = f"curl -sk -o /dev/null -w '%{{http_code}}' {shlex.quote(machine.health)}"
        deadline = time.monotonic() + _HEALTH_WAIT
        while shell(machine, probe, check=False) != "200":
            if time.monotonic() > deadline:
                raise FleetError(f"{machine.name}: {machine.health} not healthy after restart")
            time.sleep(2)
    restarts = shell(
        machine, f"systemctl show -p NRestarts --value {shlex.quote(machine.service)}", check=False
    )
    if restarts not in ("", "0"):
        raise FleetError(f"{machine.name}: {machine.service} restarted itself {restarts} times")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    up = sub.add_parser("upgrade")
    up.add_argument("version")
    up.add_argument("names", nargs="*", help="machines to upgrade (default: all)")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    try:
        machines = load(config_path())
        if args.command == "status":
            broken = 0
            for machine in machines:
                status = remote_status(machine)
                print(status_line(machine.name, installed(machine), status), flush=True)
                if status["mode"] == "remote" and status.get("reachable") != "yes":
                    broken += 1
            return 1 if broken else 0
        chosen = [m for m in machines if not args.names or m.name in args.names]
        unknown = set(args.names) - {m.name for m in machines}
        if unknown:
            raise FleetError(f"unknown machines: {', '.join(sorted(unknown))}")
        failed = 0
        for machine in chosen:
            print(f"{machine.name}: {installed(machine)} -> {args.version}", flush=True)
            try:
                upgrade(machine, args.version)
                print(f"{machine.name}: ok", flush=True)
            except FleetError as exc:
                failed += 1
                print(f"fleet: {exc}", file=sys.stderr)
        return 1 if failed else 0
    except FleetError as exc:
        print(f"fleet: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
