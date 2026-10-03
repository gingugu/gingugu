"""Argument parsing and usage text for `gingugu remote` (kept apart for size)."""

from __future__ import annotations

import argparse
from typing import Any

USAGE = """\
gingugu remote - choose which brain this machine talks to

Usage:
  gingugu remote status                 Show the active brain (local by default).
  gingugu remote on URL                 Use the brain at URL (needs a login first).
  gingugu remote off                    Go back to the local brain.
  gingugu remote login URL --ssh USER@HOST [--name NAME] [--key PATH]
                                        Mint this machine's token over SSH into
                                        the OS keychain. Does not switch brains.
                                        Key defaults to ~/.ssh/gingugu_mint.

MEMORY_REMOTE_URL overrides the machine setting for one client; "off" forces local.
"""


class _UsageError(Exception):
    """argparse rejected the arguments."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:  # type: ignore[override]
        raise _UsageError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="gingugu remote", usage=USAGE, add_help=False)
    parser.add_argument("-h", "--help", action="store_true")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("status", add_help=False)
    on = sub.add_parser("on", add_help=False)
    on.add_argument("url")
    sub.add_parser("off", add_help=False)
    login = sub.add_parser("login", add_help=False)
    login.add_argument("url")
    login.add_argument("--ssh", required=True)
    login.add_argument("--name")
    login.add_argument("--key")
    return parser
