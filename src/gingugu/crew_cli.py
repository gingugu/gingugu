"""``gingugu crew``: bring a crew mate aboard a shared brain, and see one off.

A crew mate is another agent, on its own machine with its own local brain,
that shares a brain with the crew through a scoped token - by default write on
``crew`` and nothing else. The token's name is the mate's name on the board.

On the brain host, as the brain's service user:

    gingugu crew add NAME [--ns crew=write]   the mate's token + its key line
    gingugu crew remove NAME                  revoke: every token it derived dies now
    gingugu crew list

On the mate's machine (a local brain; MEMORY_PERSONA is the mate's name):

    gingugu crew join URL --ssh USER@HOST --name NAME [--key PATH]
    gingugu crew leave
    gingugu crew status
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import crew, remote
from .serve_tokens import TokenStore, default_path, parse_grant_spec

WRAPPER = "/usr/local/bin/gingugu-mint-crew"
WAITING = 3  # join: the captain has not added the key yet - not a failure
_NAME_RE = remote._NAME_RE


def _run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, stdin=subprocess.DEVNULL, **kwargs)  # noqa: S603


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gingugu crew", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add")
    add.add_argument("name")
    add.add_argument("--ns", default=f"{crew.NAMESPACE}=write")
    add.add_argument("--wrapper", default=WRAPPER)
    sub.add_parser("remove").add_argument("name")
    sub.add_parser("list")
    join = sub.add_parser("join")
    join.add_argument("url")
    join.add_argument("--ssh", required=True, help="USER@HOST of the brain")
    join.add_argument("--name", required=True, help="your name: your persona and board name")
    join.add_argument("--key", default=str(Path.home() / ".ssh" / "gingugu_crew"))
    sub.add_parser("leave")
    sub.add_parser("status")
    return p


# --- brain side ----------------------------------------------------------------


def _add(args: argparse.Namespace) -> int:
    if not _NAME_RE.fullmatch(args.name):
        return _fail(f"invalid name {args.name!r}: letters, digits, '.', '_' or '-'", 2)
    TokenStore(default_path()).add(args.name, parse_grant_spec(args.ns), crew=True)
    print(f"{args.name} is on the crew ({args.ns}).")
    print(f"Append this line to the brain admin's ~/.ssh/authorized_keys, with {args.name}'s")
    print("public key (they print it on their first `gingugu crew join`):\n")
    print(f'restrict,command="{args.wrapper} {args.name}" <their ssh-ed25519 public key>')
    print(f'\n{args.wrapper} must exec: gingugu token ssh-mint --name "$1" --crew')
    print("as the brain's service user. Then tell them to run `gingugu crew join` again.")
    return 0


def _remove(args: argparse.Namespace) -> int:
    store = TokenStore(default_path())
    if not any(e["name"] == args.name and e["crew"] for e in store.list()):
        return _fail(f"{args.name!r} is not on the crew")
    store.revoke(args.name)
    print(f"{args.name} is off the crew: their token and every token it derived are dead.")
    print(f"Now delete their line from the brain admin's ~/.ssh/authorized_keys ({args.name}).")
    return 0


def _list() -> int:
    mates = [e for e in TokenStore(default_path()).list() if e["crew"]]
    for e in mates:
        grant = ",".join(f"{ns}={lvl}" for ns, lvl in e["namespaces"].items())
        print(f"{e['name']}  {grant}  {e['created_at']}")
    if not mates:
        print("no crew")
    return 0


# --- mate side -----------------------------------------------------------------


def _ensure_key(key: Path, name: str) -> bool:
    """True when the key already existed; False when it was just made."""
    if key.exists():
        return True
    key.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    comment = f"gingugu-crew@{name}"
    _run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(key)], check=True)
    return False


def _ask_the_captain(key: Path, name: str) -> int:
    pub = Path(f"{key}.pub").read_text(encoding="utf-8").strip()
    print("Send this to the captain. They run `gingugu crew add", name + "` on the brain and")
    print("put this key in its authorized_keys; then run this same `crew join` again:\n")
    print(pub)
    return WAITING


def _join(args: argparse.Namespace) -> int:
    if not _NAME_RE.fullmatch(args.name):
        return _fail(f"invalid name {args.name!r}: letters, digits, '.', '_' or '-'", 2)
    if remote.active() is not None:
        return _fail("this machine's whole brain is remote; a crew mate keeps a local one", 2)
    url = remote.normalize_url(args.url)
    crew._require_tls(url)
    key = Path(args.key).expanduser()
    if not _ensure_key(key, args.name):
        return _ask_the_captain(key, args.name)
    login = argparse.Namespace(url=url, name=args.name, ssh=args.ssh, key=str(key))
    if remote._login(login) != 0:
        print("\nNot aboard yet. If the captain has not added you, this is why:")
        return _ask_the_captain(key, args.name)
    url = remote_url_after_login(url)
    crew.link(url)
    exe = shutil.which("gingugu") or "gingugu"
    print(f"\nAboard: linked to the crew brain at {url} as {args.name}.")
    print("Add the crew server to Claude Code (one line), then restart it:\n")
    print(
        f"claude mcp add --scope user crew -e MEMORY_REMOTE_URL={url} "
        f"-e MEMORY_NAMESPACE={crew.NAMESPACE} -e MEMORY_PERSONA={args.name} -- {exe}"
    )
    print('\nAnd in ~/.claude/settings.json under "env", so your hooks know who and where:\n')
    print(f'  "MEMORY_PERSONA": "{args.name}",\n  "MEMORY_CREW_URL": "{url}"')
    return 0


def remote_url_after_login(url: str) -> str:
    """Login may upgrade an http URL to the brain's pinned https one."""
    from . import remote_tls

    https = remote_tls.https_of(url)
    return https if https != url and remote_tls.load_pin(https) is not None else url


def _leave() -> int:
    target = crew.active()
    if target is None:
        print("not on a crew")
        return 0
    remote._kr_delete(target.url)
    crew.unlink()
    print(f"Left the crew brain at {target.url}; its token is gone from the keychain.")
    print("Remove the crew server too:  claude mcp remove crew --scope user")
    print("and ask the captain to run `gingugu crew remove <your name>` on the brain.")
    return 0


def _status() -> int:
    target = crew.active()
    if target is None:
        print("crew: none")
        return 0
    print(f"crew: {target.url} ({target.source})")
    try:
        print(f"token: {'present' if crew.token(target) else 'missing'}")
    except Exception as exc:  # noqa: BLE001 - any keyring backend failure
        print(f"token: keychain error ({exc.__class__.__name__})")
    print(f"reachable: {'yes' if remote._reachable(target.url, None) else 'no'}")
    return 0


def _fail(message: str, code: int = 1) -> int:
    print(f"gingugu crew: {message}", file=sys.stderr)
    return code


def main(argv: list[str]) -> int:
    """Entry point for ``gingugu crew``; returns the exit code."""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    handlers = {"add": _add, "remove": _remove, "join": _join}
    try:
        if args.cmd in handlers:
            return handlers[args.cmd](args)
        return {"list": _list, "leave": _leave, "status": _status}[args.cmd]()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        return _fail(str(exc))
