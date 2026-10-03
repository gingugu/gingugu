"""The token CLI: add, list, revoke, and the SSH forced-command mint.

Split from ``serve_tokens.py`` (the store) to keep both under the file limit.

``ssh-mint`` is meant to be an ``authorized_keys`` forced command. The caller
controls ``SSH_ORIGINAL_COMMAND``, so it is parsed as hostile input: exactly
``mint NAME`` with a strict NAME, nothing else. The token goes to stdout only -
never stderr, never a log - because the SSH client captures stdout.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Any

from .serve_tokens import TokenStore, default_path, parse_grant_spec

USAGE = """\
gingugu token - manage bearer tokens for `gingugu serve`

Usage:
  gingugu token add NAME --ns SPEC   Create a scoped token; it is printed once.
  gingugu token add NAME --owner     Create a full-access token: one per machine,
                                     revocable on its own. Printed once.
  gingugu token list                 Show token names and grants (never secrets).
  gingugu token revoke NAME          Delete a token; takes effect immediately.
  gingugu token ssh-mint             Forced command for authorized_keys: reads
                                     SSH_ORIGINAL_COMMAND "mint NAME", rotates
                                     that machine's owner token, prints only it.

SPEC is comma-separated name=level pairs, level read or write, name may be *:
  gingugu token add laptop-2 --ns gingugu=write,crow=read
"""

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class _UsageError(Exception):
    """argparse rejected the arguments."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:  # type: ignore[override]
        raise _UsageError(message)


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="gingugu token", usage=USAGE, add_help=False)
    parser.add_argument("-h", "--help", action="store_true")
    sub = parser.add_subparsers(dest="cmd")
    add = sub.add_parser("add", add_help=False)
    add.add_argument("name")
    add.add_argument("--ns")
    add.add_argument("--owner", action="store_true")
    sub.add_parser("list", add_help=False)
    revoke = sub.add_parser("revoke", add_help=False)
    revoke.add_argument("name")
    sub.add_parser("ssh-mint", add_help=False)
    return parser


def _usage_error(message: str) -> int:
    print(f"gingugu token: {message}\n", file=sys.stderr)
    print(USAGE, file=sys.stderr)
    return 2


def _ssh_mint(store: TokenStore) -> int:
    """Forced-command mint. Anything but ``mint NAME`` is refused, token-free."""
    words = (os.environ.get("SSH_ORIGINAL_COMMAND") or "").split()
    if len(words) != 2 or words[0] != "mint":
        print("gingugu token ssh-mint: expected exactly 'mint NAME'", file=sys.stderr)
        return 2
    name = words[1]
    if not NAME_RE.fullmatch(name):
        print("gingugu token ssh-mint: invalid machine name", file=sys.stderr)
        return 2
    token = store.add_owner(name, replace=True)
    print(token)
    return 0


def main(argv: list[str]) -> int:
    """Entry point for ``gingugu token``; returns the process exit code."""
    try:
        args = _build_parser().parse_args(argv)
    except _UsageError as exc:
        return _usage_error(str(exc))
    if args.help:
        print(USAGE)
        return 0
    if not args.cmd:
        return _usage_error("expected add, list, revoke, or ssh-mint")
    if args.cmd == "add" and bool(args.owner) == bool(args.ns):
        return _usage_error("give exactly one of --ns SPEC or --owner")

    store = TokenStore(default_path())
    try:
        if args.cmd == "add":
            if args.owner:
                token = store.add_owner(args.name)
            else:
                try:
                    grants = parse_grant_spec(args.ns)
                except ValueError as exc:
                    return _usage_error(f"--ns: {exc}")
                token = store.add(args.name, grants)
            print(f"Token {args.name!r} created. It is shown once; store it now.", file=sys.stderr)
            print(token)
        elif args.cmd == "list":
            entries = store.list()
            if not entries:
                print("no tokens")
            for e in entries:
                spec = (
                    "owner"
                    if e["owner"]
                    else ",".join(f"{ns}={lvl}" for ns, lvl in e["namespaces"].items())
                )
                print(f"{e['name']}  {spec}  {e['created_at']}")
        elif args.cmd == "ssh-mint":
            return _ssh_mint(store)
        else:
            if not store.revoke(args.name):
                print(f"gingugu token: no token named {args.name!r}", file=sys.stderr)
                return 1
            print(f"Revoked {args.name!r}.")
    except (ValueError, OSError) as exc:
        print(f"gingugu token: {exc}", file=sys.stderr)
        return 1
    return 0
