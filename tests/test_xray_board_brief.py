"""X20: a board post that names a range arrives with its X-ray brief.

The writer opts in with a line of its own, ``xray: <range>``. The receiving side
renders the brief from its own checkout - the prompt hook's board mail for an
agent with hooks, ``board watch`` (its line and ``--exec``) for one without -
so the post carries only the ref, never paths or a brief. The ref is another
agent's text: it passes the same gate as any range before git sees it.
"""

from __future__ import annotations

import io
import json
import sys

import pytest

from gingugu import board_watch_emit, prompt_hook
from gingugu.xray import board_brief
from tests.test_board_hook import _post, local  # noqa: F401 - fixture
from tests.test_xray_brief import branched, repo  # noqa: F401 - fixtures

# --- ref_in --------------------------------------------------------------


@pytest.mark.parametrize(
    "body, ref",
    [
        ("please review\nxray: main..feature/x\n", "main..feature/x"),
        ("XRay:  HEAD~1..HEAD  ", "HEAD~1..HEAD"),
        ("xray: a..b\nxray: c..d", "a..b"),  # the first one
        ("nothing to see", None),
        ("see xray: a..b inline", None),  # must be its own line
        ("xray: a..b and more words", None),
        ("xray: --output=/tmp/x", None),
        ("xray: " + "a" * 300, None),
        ("xray:", None),
    ],
)
def test_ref_in_finds_only_a_line_of_its_own(body, ref):
    assert board_brief.ref_in(body) == ref


def test_ref_in_tolerates_a_non_string():
    assert board_brief.ref_in(None) is None


# --- attach --------------------------------------------------------------


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "data" / "x.db"))


def test_attach_renders_the_brief_from_this_checkout(branched, state):  # noqa: F811
    text = board_brief.attach("review please\nxray: HEAD~1..HEAD", str(branched), 4000)
    assert text is not None
    assert "HEAD~1..HEAD" in text.splitlines()[0]
    assert "GINGUGU X-RAY BRIEF:" in text
    assert "pkg/a.py" in text
    assert len(text) <= 4000


def test_attach_respects_max_chars(branched, state):  # noqa: F811
    text = board_brief.attach("xray: HEAD~1..HEAD", str(branched), 200)
    assert text is not None and len(text) <= 200


def test_a_clipped_brief_says_so_on_its_last_line():
    text = "\n".join(f"- line {i:03d}" for i in range(100))
    out = board_brief._fit(text, 300)
    assert len(out) <= 300
    assert out.splitlines()[-1] == board_brief.CLIPPED
    assert out.startswith("- line 000\n")
    assert all(line.startswith("- line ") for line in out.splitlines()[:-1])


def test_a_brief_that_fits_carries_no_clip_line():
    assert board_brief._fit("a\nb", 300) == "a\nb"


def test_a_budget_too_small_for_the_clip_line_is_still_respected():
    out = board_brief._fit("x" * 500, 20)
    assert len(out) <= 20


def test_a_ref_this_checkout_does_not_know_is_one_short_line(branched, state):  # noqa: F811
    text = board_brief.attach("xray: HEAD..no-such-branch", str(branched), 4000)
    assert text is not None
    assert "no-such-branch" in text and "\n" not in text and len(text) < 300


def test_attach_is_silent_without_a_ref_outside_a_repo_or_switched_off(
    branched, state, tmp_path, monkeypatch  # noqa: F811
):
    (tmp_path / "plain").mkdir()
    assert board_brief.attach("no marker here", str(branched), 4000) is None
    assert board_brief.attach("xray: HEAD~1..HEAD", str(tmp_path / "plain"), 4000) is None
    assert board_brief.attach("xray: HEAD~1..HEAD", None, 4000) is None
    monkeypatch.setenv("MEMORY_XRAY_BOARD", "off")
    assert board_brief.attach("xray: HEAD~1..HEAD", str(branched), 4000) is None


# --- the prompt hook's board mail -----------------------------------------


def _hook(cwd, session="cc-1") -> str:
    out = io.StringIO()
    from contextlib import redirect_stdout

    with redirect_stdout(out):
        prompt_hook.run({"prompt": "go", "session_id": session, "cwd": cwd})
    raw = out.getvalue().strip()
    return json.loads(raw)["hookSpecificOutput"]["additionalContext"] if raw else ""


def test_board_mail_carries_the_brief_for_a_post_that_names_a_range(local, branched):  # noqa: F811
    _post(local, body="ready for review\nxray: HEAD~1..HEAD")
    ctx = _hook(str(branched))
    assert "GINGUGU BOARD" in ctx
    assert "GINGUGU X-RAY BRIEF:" in ctx
    assert "pkg/a.py" in ctx


def test_board_mail_renders_one_brief_per_prompt(local, branched):  # noqa: F811
    _post(local, body="one\nxray: HEAD~1..HEAD")
    _post(local, body="two\nxray: HEAD~1..HEAD")
    assert _hook(str(branched)).count("GINGUGU X-RAY BRIEF:") == 1


def test_board_mail_without_a_ref_is_unchanged(local, branched):  # noqa: F811
    _post(local, body="just saying hi")
    ctx = _hook(str(branched))
    assert "GINGUGU BOARD" in ctx and "X-RAY BRIEF" not in ctx


def test_board_mail_outside_a_repo_still_arrives(local, tmp_path):  # noqa: F811
    _post(local, body="review\nxray: HEAD~1..HEAD")
    (tmp_path / "plain").mkdir()
    ctx = _hook(str(tmp_path / "plain"))
    assert "GINGUGU BOARD" in ctx and "X-RAY BRIEF" not in ctx


# --- board watch -----------------------------------------------------------


def _p(i: int, body: str) -> dict:
    return {
        "id": f"p{i}",
        "parent_id": None,
        "author": "tyrone",
        "author_origin": None,
        "recipient": "beepboop",
        "kind": "message",
        "namespace": "tyrone",
        "body": body,
        "created_at": f"2026-10-30T00:00:0{i}",
    }


def test_line_for_carries_a_brief_after_the_fenced_body():
    line = board_watch_emit.line_for(_p(1, "hi"), brief="BRIEF TEXT")
    assert line["xray"] == "BRIEF TEXT"
    assert line["text"].endswith("BRIEF TEXT")
    assert line["text"].index('"""hi"""') < line["text"].index("BRIEF TEXT")


def test_line_for_without_a_brief_is_unchanged():
    line = board_watch_emit.line_for(_p(1, "hi"))
    assert line["xray"] is None
    assert line["text"].endswith('"""hi"""')


def test_the_watch_line_carries_the_brief(branched, state):  # noqa: F811
    out = io.StringIO()
    em = board_watch_emit.Emitter(out, io.StringIO(), cwd=str(branched))
    em.emit([_p(1, "review\nxray: HEAD~1..HEAD"), _p(2, "no ref")])
    lines = [json.loads(x) for x in out.getvalue().splitlines()]
    assert "GINGUGU X-RAY BRIEF:" in lines[0]["xray"]
    assert lines[1]["xray"] is None


def test_briefs_are_capped_per_batch(branched, state):  # noqa: F811
    out = io.StringIO()
    em = board_watch_emit.Emitter(out, io.StringIO(), cwd=str(branched))
    n = board_watch_emit.MAX_BRIEFS_PER_BATCH + 2
    em.emit([_p(i, "xray: HEAD~1..HEAD") for i in range(n)])
    lines = [json.loads(x) for x in out.getvalue().splitlines()]
    assert len(lines) == n  # every post still printed
    assert sum(1 for x in lines if x["xray"]) == board_watch_emit.MAX_BRIEFS_PER_BATCH


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX sh in --exec")
def test_exec_gets_the_brief_in_its_env(branched, state, tmp_path):  # noqa: F811
    sink = tmp_path / "env.txt"
    cmd = f'printf "%s" "$GINGUGU_POST_XRAY" > "{sink}"'
    em = board_watch_emit.Emitter(io.StringIO(), io.StringIO(), cmd, cwd=str(branched))
    em.emit([_p(1, "xray: HEAD~1..HEAD")])
    assert "GINGUGU X-RAY BRIEF:" in sink.read_text()
