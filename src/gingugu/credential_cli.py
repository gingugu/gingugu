"""`gingugu credential`: this machine's vault by hand, for a person at a terminal.

The way out when no client can show the confirmation ``credential_delete``
asks for (``confirm``). Typing the name back at a real terminal is that
confirmation, so a delete refuses a stdin that is not a TTY and there is no
``--yes``: piping an answer in is not confirming. It stops accidents, not a
determined agent with a shell - that agent can reach the keychain directly.

The vault is always this machine's - also in remote mode, where the proxy
serves it from the same DB (``proxy_vault``). Secret values are never printed,
and names (model-supplied, via ``credential_store``) only as ``confirm.shown``
renders them, so one cannot rewrite the terminal.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import keyring.errors

from .confirm import shown

USAGE = """\
gingugu credential - this machine's credential vault, by hand

Usage:
  gingugu credential list                  Show services and field names; never
                                           a secret value.
  gingugu credential delete NAME [--field F]
                                           Permanently delete a service, or one
                                           field of it, keychain secrets included.
                                           Asks you to type NAME back first, so it
                                           needs a terminal: input cannot be piped.
"""


class _UsageError(Exception):
    """argparse rejected the arguments."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:  # type: ignore[override]
        raise _UsageError(message)


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="gingugu credential", usage=USAGE, add_help=False)
    parser.add_argument("-h", "--help", action="store_true")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("list", add_help=False)
    delete = sub.add_parser("delete", add_help=False)
    delete.add_argument("name")
    delete.add_argument("--field")
    return parser


def _fail(message: str, code: int = 1) -> int:
    print(f"gingugu credential: {message}", file=sys.stderr)
    return code


def _shown(text: str) -> str:
    return shown(text, limit=200)


def _list(vault) -> int:
    entries = vault.list()
    if not entries:
        print("no credentials")
    for e in entries:
        fields = ", ".join(_shown(f) for f in [*e["secret_field_names"], *e["non_secret_fields"]])
        print(f"{_shown(e['service_name'])}  [{fields}]  {e['status']}")
    return 0


def _delete(vault, name: str, field: str | None) -> int:
    entry = next((e for e in vault.list(False) if e["service_name"] == name), None)
    if entry is None:
        return _fail(f"{_shown(name)} not found")
    if field is not None and field not in [
        *entry["secret_field_names"],
        *entry["non_secret_fields"],
    ]:
        return _fail(f"field {_shown(field)} of {_shown(name)} not found")
    if not sys.stdin.isatty():
        return _fail("delete needs a person at a terminal; input cannot be piped", 2)
    target = f"field {_shown(field)} of {_shown(name)}" if field else f"{_shown(name)}"
    print(f"Permanently delete {target}{'' if field else ' and all its fields'}?")
    print("This cannot be undone.")
    try:
        typed = input("Type the service name to confirm: ")
    except EOFError:
        typed = ""
    if typed.strip() != name:
        return _fail("not confirmed; nothing deleted")
    try:
        if not vault.delete(name, field):
            return _fail(f"{_shown(name)} not found")
    except keyring.errors.KeyringError as exc:
        # Secrets go one at a time before the row: some may be gone already, and
        # the entry stays listed. Running it again finishes the job.
        return _fail(f"the keychain refused ({type(exc).__name__}); unlock it and run this again")
    print(f"Deleted {target}.")
    return 0


def main(argv: list[str]) -> int:
    """Entry point for ``gingugu credential``; returns the process exit code."""
    try:
        args = _build_parser().parse_args(argv)
    except _UsageError as exc:
        print(f"gingugu credential: {exc}\n\n{USAGE}", file=sys.stderr)
        return 2
    if args.help:
        print(USAGE)
        return 0
    if not args.cmd:
        print(f"gingugu credential: expected list or delete\n\n{USAGE}", file=sys.stderr)
        return 2

    from .config import load_config
    from .credentials import CredentialVault
    from .database import Database

    config = load_config()
    if not config.credentials_enabled:
        return _fail("the vault is off on this machine (MEMORY_CREDENTIALS_ENABLED=false)", 2)
    db = Database(config.db_path)
    try:
        vault = CredentialVault(db.connect())
        if args.cmd == "list":
            return _list(vault)
        return _delete(vault, args.name, args.field)
    except (ValueError, OSError) as exc:
        return _fail(str(exc))
    finally:
        db.close()
