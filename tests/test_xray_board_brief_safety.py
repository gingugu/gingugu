"""X20 under a hostile or heavy post: the brief must never cost the receiver its mail.

The ref is another agent's text and the brief runs inside the prompt hook, under
its timeout. So a ref git rejects fails before any graph is built, git itself has
a deadline, a build that was killed is not retried in that session, and a post is
only marked shown once its mail - brief included - has been rendered.
"""

from __future__ import annotations

import io
import subprocess

import pytest

from gingugu import board_hook, board_watch_emit, prompt_hook
from gingugu.xray import board_brief, brief, hook_common, scan
from gingugu.xray.scan import XrayError
from tests.test_board_hook import _post, local  # noqa: F401 - fixture
from tests.test_xray_brief import branched, repo  # noqa: F401 - fixtures


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "data" / "x.db"))


def test_a_ref_git_rejects_fails_before_any_graph_is_built(
    branched, state, monkeypatch  # noqa: F811
):
    def no_build(*a, **k):
        raise AssertionError("graph built for a ref git rejects")

    monkeypatch.setattr(hook_common, "graph", no_build)
    with pytest.raises(XrayError):
        brief.answer(str(branched), "HEAD..no-such-branch", None)


def test_the_session_reaches_the_graph_build(branched, state, monkeypatch):  # noqa: F811
    seen = {}
    real = hook_common.graph

    def spy(root, state_dir, session_id=None):
        seen["session"] = session_id
        return real(root, state_dir, session_id)

    monkeypatch.setattr(hook_common, "graph", spy)
    brief.answer(str(branched), "HEAD~1..HEAD", None, session_id="cc-9")
    assert seen["session"] == "cc-9"
    board_brief.attach("xray: HEAD~1..HEAD", str(branched), 4000, session_id="cc-8")
    assert seen["session"] == "cc-8"


def test_board_mail_passes_its_session_to_the_brief(local, branched, monkeypatch):  # noqa: F811
    seen = {}

    def spy(body, cwd, max_chars, session_id=None):
        seen["session"] = session_id
        return "BRIEF"

    monkeypatch.setattr(board_brief, "attach", spy)
    _post(local, body="xray: HEAD~1..HEAD")
    out = io.StringIO()
    from contextlib import redirect_stdout

    with redirect_stdout(out):
        prompt_hook.run({"prompt": "go", "session_id": "cc-7", "cwd": str(branched)})
    assert seen["session"] == "cc-7"


def test_git_has_a_deadline(tmp_path, monkeypatch):
    def slow(*a, **k):
        assert k.get("timeout") == 3
        raise subprocess.TimeoutExpired(cmd="git", timeout=3)

    monkeypatch.setattr(scan.subprocess, "run", slow)
    with pytest.raises(XrayError):
        scan.git(tmp_path, "diff", timeout=3)


def test_the_range_diff_runs_under_the_deadline(branched, monkeypatch):  # noqa: F811
    seen = []
    real = scan.git

    def spy(cwd, *args, **kw):
        seen.append((args[0] if args else None, kw.get("timeout")))
        return real(cwd, *args, **kw)

    monkeypatch.setattr(scan, "git", spy)
    brief.changes(branched, rev_range="HEAD~1..HEAD")
    assert ("diff", brief.GIT_TIMEOUT_S) in seen


def test_a_post_is_not_marked_shown_until_its_mail_is_rendered(
    local, branched, tmp_path, monkeypatch  # noqa: F811
):
    """A hook killed mid-brief prints nothing; the post must come back next prompt."""
    from gingugu.config import load_config

    class Killed(BaseException):
        pass

    def killed(*a, **k):
        raise Killed

    _post(local, body="review\nxray: HEAD~1..HEAD")
    db = load_config().db_path
    real = board_brief.attach
    monkeypatch.setattr(board_brief, "attach", killed)
    with pytest.raises(Killed):
        board_hook.mail(db, "cc-1", str(branched))
    monkeypatch.setattr(board_brief, "attach", real)
    again = board_hook.mail(db, "cc-1", str(branched))
    assert again is not None and again[1] == 1


def test_the_watch_brief_is_capped_for_exec(branched, state, monkeypatch):  # noqa: F811
    seen = {}

    def spy(body, cwd, max_chars, session_id=None):
        seen["max"] = max_chars
        return "BRIEF"

    monkeypatch.setattr(board_brief, "attach", spy)
    em = board_watch_emit.Emitter(io.StringIO(), io.StringIO(), cwd=str(branched))
    em.emit(
        [
            {
                "id": "p1",
                "parent_id": None,
                "author": "tyrone",
                "author_origin": None,
                "recipient": "beepboop",
                "kind": "message",
                "namespace": "tyrone",
                "body": "xray: HEAD~1..HEAD",
                "created_at": "2026-10-30T00:00:01",
            }
        ]
    )
    assert seen["max"] == board_watch_emit.BRIEF_CHARS == board_hook.BRIEF_CHARS
