"""X11, lean: recognise a test run by its command and read failed test files from its output.

Covers pytest, vitest, jest (and ``npm|pnpm|yarn|bun test``), ``go test`` and
``gotestsum``. Only a test file's path is ever kept from output: a log line or a
stack trace may carry a host, a token or a home directory, and none of that goes
on disk. Go names a file by basename only, joined to the failing package's import
path; vitest and jest print a path relative to where they ran.
"""

from __future__ import annotations

import json
import re

# A command at the start, after a separator, or (pytest) after `run` / `-m`, past env
# assignments. `grep vitest`, `cat jest.config.js` and `go vet` are arguments or other
# commands, not runs.
_START = r"(?:^|&&|\|\||[;|])\s*(?:\w+=\S*\s+)*(?:\S*/)?"
_PYTEST = re.compile(
    r"(?:^|&&|\|\||[;|]|\brun|\s-m)\s*(?:\w+=\S*\s+)*(?:\S*/)?(?:pytest|py\.test)(?=\s|$)"
)
_RUNNERS = re.compile(
    _START + r"(?:"
    r"(?:vitest|jest)"
    r"|(?:npx|pnpm|yarn|bunx)\s+(?:(?:exec|dlx)\s+)?(?:-\S+\s+)*(?:vitest|jest)"
    r"|(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test"
    r"|go\s+test"
    r"|gotestsum"
    r")(?=\s|$)"
)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_PATH = r"[\w./\\@+\[\]-]+"
# Only a test file's path is kept from output: a log line that happens to start
# with ERROR must not put its next word (a host, a token) on disk for good.
_PYTEST_FAILED = re.compile(r"^(?:FAILED|ERROR) ([\w./\\-]+?\.py)(?:::|\s|$)", re.MULTILINE)
_JS_FAILED = re.compile(
    rf"^[ \t]*FAIL[ \t]+(?:\|[^|\n]*\|[ \t]+)?({_PATH}\.[cm]?[jt]sx?)(?=\s|$)", re.MULTILINE
)
_GO_PKG = r"[\w./@+~-]+"
_GO_FAIL = re.compile(rf"^FAIL\t({_GO_PKG})(?=\s|$)")
_GO_END = re.compile(r"^(?:ok  |\?   |FAIL)\t")
_GO_FILE = re.compile(r"(?<![\w.-])([\w.-]+_test\.go):\d+")


def is_test_run(command: str) -> bool:
    """True when the command is a pytest, vitest, jest, npm-style ``test`` or Go run."""
    return bool(_PYTEST.search(command) or _RUNNERS.search(command))


def _go_text(text: str) -> set[str]:
    """Failing Go packages' test files, from plain ``go test`` output."""
    found: set[str] = set()
    block: set[str] = set()
    for line in text.splitlines():
        if _GO_END.match(line):
            fail = _GO_FAIL.match(line)
            if fail:
                found.update(f"{fail.group(1)}/{name}" for name in block)
            block = set()
            continue
        block.update(_GO_FILE.findall(line))
    return found


def _go_json(text: str) -> set[str]:
    """Failing Go packages' test files, from ``go test -json`` records."""
    files: dict[str, set[str]] = {}
    failed: set[str] = set()
    for line in text.splitlines():
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except (ValueError, RecursionError):
            continue
        pkg = rec.get("Package") if isinstance(rec, dict) else None
        if not isinstance(pkg, str) or not re.fullmatch(_GO_PKG, pkg):
            continue
        out = rec.get("Output")
        if isinstance(out, str):
            files.setdefault(pkg, set()).update(_GO_FILE.findall(out))
        if rec.get("Action") == "fail" and not rec.get("Test"):
            failed.add(pkg)
    return {f"{pkg}/{name}" for pkg in failed for name in files.get(pkg, ())}


def failed_tests(text: str) -> list[str]:
    """Failed test files named by pytest, vitest, jest or ``go test`` output, sorted."""
    text = _ANSI.sub("", text)
    found = {m.group(1).replace("\\", "/") for m in _PYTEST_FAILED.finditer(text)}
    found.update(m.group(1).replace("\\", "/") for m in _JS_FAILED.finditer(text))
    found |= _go_text(text) | _go_json(text)
    return sorted(found)


def same_file(failed: str, predicted: str) -> bool:
    """Equal, or one ends with the other on a ``/`` boundary (runner cwd, Go import path)."""
    return (
        failed == predicted or predicted.endswith("/" + failed) or failed.endswith("/" + predicted)
    )
