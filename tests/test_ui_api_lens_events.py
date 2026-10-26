"""The Explorer's query lens (``/search``) and synapse feed (``/events``).

The lens runs the same hybrid ranking the agent gets from ``memory_recall``,
with the score breakdown attached, but as an observer: no access credit, no
``query_log`` row, no spreading activation. The events feed reads the agent's
``access_log`` and ``query_log`` rows in time order behind a cursor, live or
over a replay window.
"""

from __future__ import annotations

from gingugu.ui_api import UiEnv, dispatch
from tests.test_embeddings import FakeEmbedder
from tests.ui_api_fixtures import T1, T2, T3, T4, snapshot_signals


def _get(seeded, path, params=None):
    return dispatch(path, params or {}, UiEnv(db_path=seeded.db_path, embedder=FakeEmbedder))


# --- lens ----------------------------------------------------------------------


def test_search_ranks_and_explains(seeded):
    status, body = _get(seeded, "/search", {"q": "stack parent branch"})
    assert status == 200 and body["ok"] is True
    assert body["count"] == len(body["hits"]) > 0
    assert body["hits"][0]["id"] == seeded.ids["rule"]
    for hit in body["hits"]:
        assert {"id", "title", "namespace", "type", "confidence", "score"} <= set(hit)
        assert isinstance(hit["score_breakdown"], dict)


def test_search_scoped_to_namespaces(seeded):
    _, body = _get(seeded, "/search", {"q": "body text", "ns": "gingugu"})
    assert body["hits"]
    assert {h["namespace"] for h in body["hits"]} == {"gingugu"}


def test_search_accepts_a_namespace_list(seeded):
    _, body = _get(seeded, "/search", {"q": "body text", "ns": "crow,gingugu"})
    assert {h["namespace"] for h in body["hits"]} == {"crow", "gingugu"}


def test_search_needs_a_query_and_a_sane_limit(seeded):
    assert _get(seeded, "/search", {})[0] == 400
    assert _get(seeded, "/search", {"q": "  "})[0] == 400
    assert _get(seeded, "/search", {"q": "body text", "limit": "lots"})[0] == 400
    status, body = _get(seeded, "/search", {"q": "body text", "limit": "100000"})
    assert status == 200 and body["count"] <= 100


def test_search_unknown_namespace_is_400(seeded):
    assert _get(seeded, "/search", {"q": "body text", "ns": "nope"})[0] == 400


def test_search_is_an_observer(seeded):
    before = snapshot_signals(seeded.db_path)
    assert _get(seeded, "/search", {"q": "stack parent"})[0] == 200
    assert snapshot_signals(seeded.db_path) == before


# --- events --------------------------------------------------------------------


def _shape(ev: dict) -> tuple:
    return (ev["kind"], ev["at"])


def test_events_without_a_cursor_start_at_now(seeded):
    status, body = _get(seeded, "/events")
    assert status == 200 and body["ok"] is True
    assert body["events"] == []
    assert body["cursor"] == T4


def test_events_after_a_cursor_in_time_order(seeded):
    _, body = _get(seeded, "/events", {"after": T1})
    assert [_shape(e) for e in body["events"]] == [
        ("access", T2),
        ("query", T3),
        ("access", T4),
    ]
    assert body["cursor"] == T4


def test_event_payloads(seeded):
    _, body = _get(seeded, "/events", {"from": T1, "to": T3})
    query, access = body["events"]
    assert query == {
        "kind": "query",
        "at": T1,
        "tool": "recall",
        "query": "stack parent",
        "namespaces": ["crow"],
        "result_ids": [seeded.ids["rule"], seeded.ids["lesson"]],
        "session_id": "sess-1",
    }
    assert access == {
        "kind": "access",
        "at": T2,
        "memory_id": seeded.ids["rule"],
        "context": "recall",
    }


def test_events_replay_window_is_half_open(seeded):
    _, body = _get(seeded, "/events", {"from": T2, "to": T4})
    assert [_shape(e) for e in body["events"]] == [("access", T2), ("query", T3)]


def test_events_limit_advances_the_cursor_only_as_far_as_returned(seeded):
    _, first = _get(seeded, "/events", {"after": T1, "limit": "1"})
    assert [_shape(e) for e in first["events"]] == [("access", T2)]
    assert first["cursor"] == T2
    _, rest = _get(seeded, "/events", {"after": first["cursor"]})
    assert [_shape(e) for e in rest["events"]] == [("query", T3), ("access", T4)]


def test_events_quiet_cursor_stays_put(seeded):
    _, body = _get(seeded, "/events", {"after": T4})
    assert body["events"] == [] and body["cursor"] == T4


def test_events_reject_garbage_timestamps(seeded):
    assert _get(seeded, "/events", {"after": "yesterday"})[0] == 400
    assert _get(seeded, "/events", {"from": T1})[0] == 400  # a window needs both ends
    assert _get(seeded, "/events", {"after": T1, "limit": "-3"})[0] == 400


# --- cursor correctness under same-timestamp batches ---------------------------
# access.record stamps every memory a recall returned with ONE timestamp, so a
# page boundary landing inside that batch must never strand the rest of it.

T5 = "2026-10-23T12:00:00.000000+00:00"


def _batch(seeded, n: int) -> None:
    import sqlite3

    conn = sqlite3.connect(seeded.db_path)
    conn.executemany(
        "INSERT INTO access_log (id, memory_id, accessed_at, context) VALUES (?, ?, ?, ?)",
        [(f"b{i}", seeded.ids["rule"], T5, "sess-2") for i in range(n)],
    )
    conn.commit()
    conn.close()


def _drain(seeded, params) -> list[dict]:
    seen: list[dict] = []
    for _ in range(50):
        _, body = _get(seeded, "/events", params)
        if not body["events"]:
            return seen
        seen += body["events"]
        params = {**params, "after": body["cursor"]}
        params.pop("from", None)
    raise AssertionError("cursor never drained")


def test_paging_never_splits_a_same_timestamp_batch(seeded):
    _batch(seeded, 5)
    seen = _drain(seeded, {"after": T4, "limit": "3"})
    assert len(seen) == 5 and {e["at"] for e in seen} == {T5}


def test_a_batch_bigger_than_the_limit_comes_back_whole(seeded):
    _batch(seeded, 7)
    _, body = _get(seeded, "/events", {"after": T4, "limit": "2"})
    assert len(body["events"]) == 7
    assert body["cursor"] == T5


def test_page_boundary_before_a_batch_stops_short_of_it(seeded):
    _batch(seeded, 4)
    _, body = _get(seeded, "/events", {"after": T1, "limit": "4"})
    # T2, T3, T4 fit; the fourth slot would split the T5 batch, so it is left out.
    assert [e["at"] for e in body["events"]] == [T2, T3, T4]
    assert body["cursor"] == T4


def test_replay_window_pages_with_after_and_to(seeded):
    _batch(seeded, 3)
    end = "2026-10-24T00:00:00.000000+00:00"
    seen = _drain(seeded, {"from": T1, "to": end, "limit": "2"})
    assert [e["at"] for e in seen] == [T1, T2, T3, T4, T5, T5, T5]


def test_after_and_from_are_exclusive(seeded):
    assert _get(seeded, "/events", {"after": T1, "from": T1, "to": T4})[0] == 400
