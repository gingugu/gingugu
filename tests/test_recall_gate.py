"""The involuntary-recall gate.

Every test here is offline and deterministic: synthetic candidates with
hand-chosen similarities, no model, no network, no real brain. The gate is
pure arithmetic precisely so it can be pinned this way - the sweep that feeds
it owns everything that touches the world.
"""

from __future__ import annotations

import json

import pytest

from gingugu.prompt_hook import (
    config_from_env,
    enabled,
    load_suppressed,
    namespaces_for,
    save_suppressed,
)
from gingugu.recall_gate import (
    Candidate,
    GateConfig,
    is_worth_embedding,
    lexical_terms,
    render,
    select,
    strip_affect,
)


def make(idx: str, sim: float, *, mtype: str = "preference") -> Candidate:
    return Candidate(
        id=idx,
        title=f"memory {idx}",
        summary="body",
        namespace="crow",
        type=mtype,
        similarity=sim,
    )


def field(n: int, sim: float = 0.50) -> list[Candidate]:
    """A flat background field, so the median is well defined and low."""
    return [make(f"bg{i}", sim) for i in range(n)]


# --- stage one: the cheap gate -------------------------------------------


@pytest.mark.parametrize("prompt", ["go", "lfg", "yup!", "ship it", "ok.", "   "])
def test_acknowledgements_never_reach_the_encoder(prompt):
    assert is_worth_embedding(prompt) is False


def test_short_prompts_are_rejected_before_the_model_loads():
    assert is_worth_embedding("fix the bug") is False


def test_a_real_request_passes_stage_one():
    assert is_worth_embedding("lets fix the pinned tier ordering bug in memory_context")


def test_min_chars_is_tunable():
    assert is_worth_embedding("fix the bug", min_chars=5) is True


# --- affect stripping -----------------------------------------------------


def test_strip_affect_removes_register_and_keeps_subject():
    out = strip_affect("OMG nice work matey! fix the pinned tier ordering")
    assert "matey" not in out.lower()
    assert "omg" not in out.lower()
    assert "pinned tier ordering" in out


def test_strip_affect_falls_back_when_everything_was_affect():
    assert strip_affect("omg lol nice") == "omg lol nice"


def test_lexical_terms_keep_identifiers_dedupe_and_drop_stubs():
    """Identifiers survive tokenisation; one- and two-character words do not."""
    assert lexical_terms("memory_context memory_context recall.py a bb") == [
        "memory_context",
        "recall",
    ]


# --- the bar and the margin ----------------------------------------------


def test_a_hit_below_the_bar_never_fires():
    picked = select([make("a", 0.70), *field(9)], lexical_ids={"a"})
    assert picked == []


def test_a_hit_over_the_bar_with_a_real_gap_fires():
    picked = select([make("a", 0.85), *field(9)], lexical_ids={"a"})
    assert [c.id for c in picked] == ["a"]


def test_the_margin_rejects_a_high_score_in_a_uniformly_high_field():
    """The gate that matters: absolute score is meaningless without a gap.

    Every candidate clears 0.78, so a flat bar would inject three. Nothing is
    distinctive, so nothing should fire.
    """
    crowd = [make(f"c{i}", 0.82) for i in range(21)]
    assert select(crowd, lexical_ids={c.id for c in crowd}) == []


def test_margin_is_measured_against_the_median_of_the_full_sweep():
    sweep = [make("top", 0.90), *field(20, 0.60)]
    assert [c.id for c in select(sweep, lexical_ids={"top"})] == ["top"]


# --- the lexical half -----------------------------------------------------


def test_semantic_hit_without_a_lexical_match_is_rejected():
    """Voice-only matches are what the lexical requirement exists to remove."""
    picked = select([make("a", 0.88), *field(9)], lexical_ids=set())
    assert picked == []


def test_lexical_requirement_can_be_disabled():
    cfg = GateConfig(require_lexical=False)
    picked = select([make("a", 0.88), *field(9)], lexical_ids=set(), config=cfg)
    assert [c.id for c in picked] == ["a"]


def test_none_lexical_ids_means_the_filter_is_not_applied():
    picked = select([make("a", 0.88), *field(9)], lexical_ids=None)
    assert [c.id for c in picked] == ["a"]


# --- cap, suppression, ordering ------------------------------------------


def test_cap_limits_the_injection():
    hits = [make(f"h{i}", 0.90 - i * 0.001) for i in range(6)]
    picked = select([*hits, *field(30)], lexical_ids={h.id for h in hits})
    assert len(picked) == 3


def test_results_arrive_best_first():
    hits = [make("low", 0.80), make("high", 0.95), make("mid", 0.88)]
    picked = select([*hits, *field(20)], lexical_ids={"low", "high", "mid"})
    assert [c.id for c in picked] == ["high", "mid", "low"]


def test_a_memory_already_injected_this_session_is_suppressed():
    hits = [make("seen", 0.95), make("fresh", 0.90)]
    picked = select([*hits, *field(20)], lexical_ids={"seen", "fresh"}, suppressed={"seen"})
    assert [c.id for c in picked] == ["fresh"]


def test_empty_sweep_is_not_an_error():
    assert select([], lexical_ids=set()) == []


# --- rendering ------------------------------------------------------------


def test_render_is_empty_when_nothing_fired():
    assert render([]) == ""


def test_render_names_the_memory_and_how_to_expand_it():
    out = render([make("abc123", 0.87)])
    assert "abc123" in out
    assert "memory_recall" in out
    assert "0.870" in out


def test_render_marks_the_block_as_recalled_context_not_instructions():
    """Injected text arrives unrequested, so it must not read as a directive."""
    assert "not instructions" in render([make("a", 0.9)])


# --- session plumbing -----------------------------------------------------


def test_namespaces_are_the_global_plus_the_repo_directory():
    assert namespaces_for("/home/me/my-project") == ["crow", "my-project"]


def test_namespaces_do_not_duplicate_the_global_one():
    assert namespaces_for("/home/me/crow") == ["crow"]


def test_suppression_state_round_trips(tmp_path):
    db = tmp_path / "memories.db"
    save_suppressed(db, "sess-1", {"a", "b"})
    assert load_suppressed(db, "sess-1") == {"a", "b"}


def test_suppression_is_per_session(tmp_path):
    db = tmp_path / "memories.db"
    save_suppressed(db, "sess-1", {"a"})
    assert load_suppressed(db, "sess-2") == set()


def test_missing_state_reads_as_empty_not_an_error(tmp_path):
    assert load_suppressed(tmp_path / "memories.db", "never-written") == set()


def test_corrupt_state_reads_as_empty(tmp_path):
    db = tmp_path / "memories.db"
    directory = db.parent / "hook-sessions"
    directory.mkdir()
    (directory / "sess.json").write_text("{not json")
    assert load_suppressed(db, "sess") == set()


# --- configuration --------------------------------------------------------


def test_hook_is_on_by_default(monkeypatch):
    monkeypatch.delenv("MEMORY_RECALL_HOOK", raising=False)
    assert enabled() is True


@pytest.mark.parametrize("value", ["off", "0", "false", "OFF"])
def test_kill_switch(monkeypatch, value):
    monkeypatch.setenv("MEMORY_RECALL_HOOK", value)
    assert enabled() is False


def test_env_overrides_the_measured_defaults(monkeypatch):
    monkeypatch.setenv("MEMORY_RECALL_HOOK_BAR", "0.9")
    monkeypatch.setenv("MEMORY_RECALL_HOOK_MARGIN", "0.3")
    monkeypatch.setenv("MEMORY_RECALL_HOOK_CAP", "1")
    cfg = config_from_env()
    assert (cfg.bar, cfg.margin, cfg.cap) == (0.9, 0.3, 1)


def test_a_junk_env_value_falls_back_instead_of_crashing(monkeypatch):
    monkeypatch.setenv("MEMORY_RECALL_HOOK_BAR", "not-a-float")
    assert config_from_env().bar == GateConfig().bar


# --- the never-crash contract --------------------------------------------


def test_main_exits_zero_on_garbage_stdin(monkeypatch, capsys):
    from gingugu import prompt_hook

    monkeypatch.setattr("sys.stdin", _Stdin("{not json at all"))
    assert prompt_hook.main() == 0
    assert capsys.readouterr().out == ""


def test_main_exits_zero_on_empty_stdin(monkeypatch):
    from gingugu import prompt_hook

    monkeypatch.setattr("sys.stdin", _Stdin(""))
    assert prompt_hook.main() == 0


def test_main_stays_silent_on_an_acknowledgement(monkeypatch, capsys):
    from gingugu import prompt_hook

    monkeypatch.setattr("sys.stdin", _Stdin(json.dumps({"prompt": "lfg", "cwd": "/x"})))
    assert prompt_hook.main() == 0
    assert capsys.readouterr().out == ""


def test_main_stays_silent_when_disabled(monkeypatch, capsys):
    from gingugu import prompt_hook

    monkeypatch.setenv("MEMORY_RECALL_HOOK", "off")
    monkeypatch.setattr(
        "sys.stdin", _Stdin(json.dumps({"prompt": "a real question about memory_context"}))
    )
    assert prompt_hook.main() == 0
    assert capsys.readouterr().out == ""


class _Stdin:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text
