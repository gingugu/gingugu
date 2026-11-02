"""The prompt hook recalls on what the user said, not on harness notices.

Measured 2026-11-02 on the real brain: 1,218 of 2,481 prompt-hook queries were
``<task-notification>`` events (a background command finished, a monitor
fired), not prompts - each one a recall against the brain, 44 of them injecting
memories, and every one a junk row in the query log the paraphrase set is built
from. Those blocks are stripped before the gate; a prompt that is nothing else
recalls nothing, and one that carries real text around them recalls on that.
"""

from __future__ import annotations

import pytest

from gingugu import prompt_hook
from gingugu.recall_gate import strip_notifications

NOTICE = (
    "<task-notification>\n<task-id>b1</task-id>\n<status>completed</status>\n"
    "<summary>Background command finished</summary>\n</task-notification>"
)


def test_a_notice_alone_strips_to_nothing():
    assert strip_notifications(NOTICE) == ""
    assert strip_notifications(f"  {NOTICE}\n{NOTICE}  ") == ""


def test_text_around_a_notice_is_kept():
    out = strip_notifications(f"{NOTICE}\nwhy did the ledger replay fail?")
    assert out == "why did the ledger replay fail?"


def test_an_unclosed_notice_is_dropped_to_the_end():
    assert strip_notifications("real question\n<task-notification><task-id>x") == "real question"


def test_other_tags_are_left_alone():
    pasted = "<pasted_content>the stack trace</pasted_content> what is this?"
    assert strip_notifications(pasted) == pasted


def test_a_prompt_without_a_notice_is_unchanged():
    assert strip_notifications("  plain prompt ") == "  plain prompt "


def test_a_notice_only_prompt_never_reaches_recall(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("recall ran on a harness notice")

    monkeypatch.setattr(prompt_hook, "namespaces_for", boom)
    assert prompt_hook._recall(NOTICE * 3, "s", "/tmp", app=None) is None


def test_recall_sees_only_the_real_text(monkeypatch):
    seen = []

    def capture(cwd):
        return []  # stop right after the gate

    monkeypatch.setattr(prompt_hook, "namespaces_for", capture)
    monkeypatch.setattr(prompt_hook, "is_worth_embedding", lambda p, **k: seen.append(p) or True)
    prompt_hook._recall(f"{NOTICE}\nwhy did the ledger replay fail?", "s", "/tmp", app=None)
    assert seen == ["why did the ledger replay fail?"]


@pytest.mark.parametrize("prompt", ["", "   ", NOTICE])
def test_run_with_a_notice_still_delivers_board_mail(monkeypatch, tmp_path, prompt):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.setattr("gingugu.board_hook.mail", lambda *a: ("BOARD MAIL", 1))
    out = []
    monkeypatch.setattr(prompt_hook, "_emit", lambda ctx, n, p=0: out.append((ctx, n, p)))
    prompt_hook.run({"prompt": prompt, "session_id": "s", "cwd": str(tmp_path)})
    assert out == [("BOARD MAIL", 0, 1)]


def test_the_brain_strips_a_notice_an_older_client_sends(monkeypatch):
    from types import SimpleNamespace

    from gingugu import serve_hooks
    from gingugu.prompt_hook import config_from_env

    seen = []
    monkeypatch.setattr(
        "gingugu.recall_pick.pick", lambda db, emb, text, *a: seen.append(text) or []
    )
    ctx = SimpleNamespace(
        store=SimpleNamespace(embedder=SimpleNamespace(enabled=True)),
        config=SimpleNamespace(db_path="x"),
    )
    cfg = config_from_env()
    assert serve_hooks._run_recall(ctx, (NOTICE, "s", ["ns"], set(), cfg))["ids"] == []
    assert seen == []
    real = f"{NOTICE}\nwhy did the ledger replay fail last night?"
    serve_hooks._run_recall(ctx, (real, "s", ["ns"], set(), cfg))
    assert seen == ["why did the ledger replay fail last night?"]
