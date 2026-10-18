"""Cut a gingugu release: gates, version bump, tag, publish, wait for PyPI.

    uv run python scripts/release.py 0.24.0 --summary "MCP SDK 2.x" --body-file notes.txt
    uv run python scripts/release.py 0.24.0 --summary "..." --dry-run

What stays a person's job, before running this: the CHANGELOG ``[Unreleased]``
entries and the ``.ai/memory.md`` Release State bullet for the new version (the
script refuses to run without both), and the commit body (``--body-file``).
Everything else is mechanical and done here, in this order:

1. Preflight: on ``main``, clean, level with ``origin/main``, a newer version,
   no tag yet.
2. Gates, the same ones ``release.yml`` runs - tests, lint, the Memory Explorer
   UI build, a wheel that bundles the UI. CI never builds the UI, so a broken UI
   would otherwise surface only after the tag is pushed.
3. Bump ``pyproject.toml``, ``uv lock``, move ``[Unreleased]`` under the new
   version, commit, annotated tag.
4. Push ``main`` and the tag (the irreversible step; ``--dry-run`` stops
   before it), watch ``release.yml``, then wait until PyPI serves the version
   to installers - its simple index lags the JSON API by minutes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_VERSION_LINE = re.compile(r'^version = "([^"]+)"$', re.MULTILINE)
_UNRELEASED = "## [Unreleased]\n"


class ReleaseError(Exception):
    """A precondition failed; the message says what to fix."""


def parse_version(text: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"\d+\.\d+\.\d+", text):
        raise ReleaseError(f"version must be MAJOR.MINOR.PATCH, got {text!r}")
    major, minor, patch = (int(part) for part in text.split("."))
    return major, minor, patch


def current_version(pyproject: str) -> str:
    match = _VERSION_LINE.search(pyproject)
    if match is None:
        raise ReleaseError('no `version = "..."` line in pyproject.toml')
    return match.group(1)


def bump_pyproject(pyproject: str, version: str) -> str:
    return _VERSION_LINE.sub(f'version = "{version}"', pyproject, count=1)


def roll_changelog(changelog: str, version: str, date: str) -> str:
    """Move the ``[Unreleased]`` entries under ``## [version] - date``.

    Leaves an empty ``[Unreleased]`` behind, separated by the file's ``---``
    rules, which is the shape every earlier release left.
    """
    start = changelog.find(_UNRELEASED)
    if start == -1:
        raise ReleaseError("CHANGELOG.md has no `## [Unreleased]` heading")
    body_start = start + len(_UNRELEASED)
    end = changelog.find("\n---\n", body_start)
    if end == -1:
        raise ReleaseError("CHANGELOG.md [Unreleased] is not closed by a `---` rule")
    entries = changelog[body_start:end].strip("\n")
    if not entries.strip():
        raise ReleaseError("CHANGELOG.md [Unreleased] is empty: nothing to release")
    if f"## [{version}]" in changelog:
        raise ReleaseError(f"CHANGELOG.md already has a [{version}] section")
    rolled = f"{_UNRELEASED}\n---\n\n## [{version}] - {date}\n\n{entries}\n"
    return changelog[:start] + rolled + changelog[end:]


def check_release_state(memory_md: str, version: str) -> None:
    if f"**{version}**" not in memory_md:
        raise ReleaseError(
            f".ai/memory.md Release State does not name **{version}** yet: "
            "write the current-version bullet first"
        )


def run(cmd: list[str], *, cwd: Path = ROOT, capture: bool = False) -> str:
    print(f"$ {' '.join(cmd)}", flush=True)
    done = subprocess.run(cmd, cwd=cwd, text=True, capture_output=capture)
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip() if capture else ""
        raise ReleaseError(f"`{' '.join(cmd)}` failed ({done.returncode}) {detail}".strip())
    return (done.stdout or "").strip() if capture else ""


def preflight(version: str) -> str:
    """Return the current version after checking the tree is releasable."""
    branch = run(["git", "branch", "--show-current"], capture=True)
    if branch != "main":
        raise ReleaseError(f"releases are cut from main, not {branch!r}")
    if run(["git", "status", "--porcelain"], capture=True):
        raise ReleaseError("working tree is not clean")
    run(["git", "fetch", "--quiet", "--tags", "origin"])
    if run(["git", "rev-parse", "HEAD"], capture=True) != run(
        ["git", "rev-parse", "origin/main"], capture=True
    ):
        raise ReleaseError("main is not level with origin/main: pull or push first")
    if run(["git", "tag", "--list", f"v{version}"], capture=True):
        raise ReleaseError(f"tag v{version} already exists")
    old = current_version((ROOT / "pyproject.toml").read_text())
    if parse_version(version) <= parse_version(old):
        raise ReleaseError(f"{version} is not newer than the current {old}")
    roll_changelog((ROOT / "CHANGELOG.md").read_text(), version, "check")
    check_release_state((ROOT / ".ai" / "memory.md").read_text(), version)
    return old


def gates() -> None:
    run(["uv", "run", "pytest", "-q"])
    run(["uv", "run", "ruff", "check", "."])
    run(["uv", "run", "black", "--check", "."])
    run(["npm", "ci"], cwd=ROOT / "ui")
    run(["npm", "run", "build"], cwd=ROOT / "ui")
    with tempfile.TemporaryDirectory() as out:
        run(["uv", "build", "--wheel", "--out-dir", out])
        wheels = list(Path(out).glob("*.whl"))
        if len(wheels) != 1:
            raise ReleaseError(f"expected one wheel, built {len(wheels)}")
        with zipfile.ZipFile(wheels[0]) as wheel:
            if not any(n.endswith("_ui_dist/index.html") for n in wheel.namelist()):
                raise ReleaseError("the wheel does not bundle the Memory Explorer UI")


def write_release(version: str, summary: str, body: str) -> None:
    pyproject = ROOT / "pyproject.toml"
    pyproject.write_text(bump_pyproject(pyproject.read_text(), version))
    run(["uv", "lock"])
    changelog = ROOT / "CHANGELOG.md"
    today = dt.date.today().isoformat()
    changelog.write_text(roll_changelog(changelog.read_text(), version, today))
    run(["git", "add", "pyproject.toml", "uv.lock", "CHANGELOG.md", ".ai/memory.md"])
    message = f"chore(release): {version} - {summary}\n\n{body}".rstrip() + "\n"
    run(["git", "commit", "-q", "-m", message])
    run(["git", "tag", "-a", f"v{version}", "-m", f"v{version}"])


def _release_run_id(tag: str, deadline: float) -> str:
    while time.monotonic() < deadline:
        out = run(
            ["gh", "run", "list", "--workflow", "release.yml", "--branch", tag]
            + ["--json", "databaseId", "--limit", "1"],
            capture=True,
        )
        runs = json.loads(out or "[]")
        if runs:
            return str(runs[0]["databaseId"])
        time.sleep(5)
    raise ReleaseError(f"no release.yml run appeared for {tag}")


def _pypi_serves(version: str) -> bool:
    url = "https://pypi.org/simple/gingugu/"
    req = urllib.request.Request(url, headers={"Accept": "text/html", "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - fixed https URL
            return f"gingugu-{version}-py3-none-any.whl" in resp.read().decode()
    except urllib.error.URLError:
        return False


def publish(version: str) -> None:
    tag = f"v{version}"
    run(["git", "push", "origin", "main"])
    run(["git", "push", "origin", tag])
    run_id = _release_run_id(tag, time.monotonic() + 120)
    run(["gh", "run", "watch", run_id, "--exit-status", "--interval", "15"])
    deadline = time.monotonic() + 900
    while not _pypi_serves(version):
        if time.monotonic() > deadline:
            raise ReleaseError(f"PyPI's index still lacks {version} after 15 minutes")
        time.sleep(15)
    print(f"released {version}: https://pypi.org/project/gingugu/{version}/")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version")
    parser.add_argument("--summary", required=True, help="commit subject after the version")
    parser.add_argument("--body-file", type=Path, help="commit message body")
    parser.add_argument("--dry-run", action="store_true", help="gates and checks only")
    args = parser.parse_args(argv)
    try:
        old = preflight(args.version)
        print(f"{old} -> {args.version}: preflight ok")
        gates()
        if args.dry_run:
            print("dry run: gates passed; nothing written or pushed")
            return 0
        body = args.body_file.read_text() if args.body_file else ""
        write_release(args.version, args.summary, body)
        publish(args.version)
    except ReleaseError as exc:
        print(f"release: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
