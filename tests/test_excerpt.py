"""Reading inside one memory: offsets, matches, and the size-only call."""

from __future__ import annotations

import json

import pytest

from gingugu.excerpt import MAX_MATCHES_CAP, clamp_range, find_matches, line_of

BODY = (
    "The release policy says nothing ships untested.\n"
    "The second line mentions the release policy again.\n"
    "The third line does not.\n"
)


def _payload(result) -> dict:
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "excerpt.db"))
    monkeypatch.setenv("MEMORY_NAMESPACE", "excerpt")
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")
    from gingugu.server import build_server

    return build_server()


async def _store(server, title: str, content: str) -> str:
    res = _payload(
        await server.call_tool(
            "memory_store", {"title": title, "content": content, "type": "workflow"}
        )
    )
    assert res["ok"]
    return res["memory"]["id"]


def test_clamp_range_bounds_and_defaults() -> None:
    assert clamp_range(100, None, None) == (0, 100)
    assert clamp_range(100, 10, 20) == (10, 20)
    assert clamp_range(100, -5, 500) == (0, 100)
    # Inverted bounds are swapped, not silently emptied.
    assert clamp_range(100, 30, 10) == (10, 30)


def test_line_of_is_one_indexed() -> None:
    assert line_of(BODY, 0) == 1
    assert line_of(BODY, BODY.index("second")) == 2
    assert line_of(BODY, BODY.index("third")) == 3


def test_find_matches_reports_offsets_and_lines() -> None:
    matches, total = find_matches(BODY, "release policy")
    assert total == 2
    assert [m["line"] for m in matches] == [1, 2]
    assert BODY[matches[0]["start"] : matches[0]["end"]] == "The release policy"[4:]


def test_find_matches_is_case_insensitive_by_default() -> None:
    assert find_matches(BODY, "RELEASE POLICY")[1] == 2
    assert find_matches(BODY, "RELEASE POLICY", case_sensitive=True)[1] == 0


def test_total_is_the_true_count_even_when_capped() -> None:
    """The cap bounds the payload; it must not bound the reported truth."""
    matches, total = find_matches("ab " * 50, "ab", max_matches=3)
    assert len(matches) == 3
    assert total == 50


def test_overlapping_needle_does_not_double_report() -> None:
    _, total = find_matches("aaaa", "aa")
    assert total == 2


def test_offsets_stay_absolute_inside_a_windowed_search() -> None:
    """A hit found inside a range is addressable in the full body, not the slice."""
    start = BODY.index("second")
    matches, total = find_matches(BODY, "release policy", start=start)
    assert total == 1
    assert BODY[matches[0]["start"] : matches[0]["end"]] == "release policy"


def test_empty_query_finds_nothing() -> None:
    assert find_matches(BODY, "") == ([], 0)


@pytest.mark.asyncio
async def test_excerpt_finds_matches_over_the_tool_surface(server) -> None:
    mid = await _store(server, "the resume", BODY)

    res = _payload(
        await server.call_tool("memory_excerpt", {"memory_id": mid, "query": "release policy"})
    )
    assert res["ok"]
    assert res["total_matches"] == 2
    assert res["truncated"] is False
    assert res["lines"] == 4  # trailing newline opens a fourth line
    assert "release policy" in res["matches"][0]["excerpt"]
    # The body itself is never shipped back on a find.
    assert "text" not in res


@pytest.mark.asyncio
async def test_excerpt_slices_by_offset(server) -> None:
    mid = await _store(server, "the resume", BODY)

    res = _payload(
        await server.call_tool("memory_excerpt", {"memory_id": mid, "start": 4, "end": 18})
    )
    assert res["text"] == "release policy"
    assert (res["start"], res["end"]) == (4, 18)


@pytest.mark.asyncio
async def test_excerpt_composes_range_and_query(server) -> None:
    mid = await _store(server, "the resume", BODY)

    res = _payload(
        await server.call_tool(
            "memory_excerpt",
            {"memory_id": mid, "query": "release policy", "start": BODY.index("The second")},
        )
    )
    assert res["total_matches"] == 1
    assert res["matches"][0]["line"] == 2


@pytest.mark.asyncio
async def test_excerpt_with_no_mode_returns_size_and_says_so(server) -> None:
    mid = await _store(server, "the resume", BODY)

    res = _payload(await server.call_tool("memory_excerpt", {"memory_id": mid}))
    assert res["length"] == len(BODY)
    assert "hint" in res
    assert "text" not in res


@pytest.mark.asyncio
async def test_excerpt_rejects_bad_arguments(server) -> None:
    mid = await _store(server, "the resume", BODY)

    over_cap = _payload(
        await server.call_tool(
            "memory_excerpt", {"memory_id": mid, "query": "x", "max_matches": MAX_MATCHES_CAP + 1}
        )
    )
    assert over_cap["ok"] is False

    blank = _payload(await server.call_tool("memory_excerpt", {"memory_id": mid, "query": "   "}))
    assert blank["ok"] is False

    missing = _payload(await server.call_tool("memory_excerpt", {"memory_id": "nope"}))
    assert missing["ok"] is False
    assert "not found" in missing["error"]


@pytest.mark.asyncio
async def test_excerpt_credits_a_real_access(server) -> None:
    mid = await _store(server, "the resume", BODY)

    before = _payload(await server.call_tool("memory_search", {"ids": mid}))["memories"][0]
    await server.call_tool("memory_excerpt", {"memory_id": mid, "query": "release"})
    after = _payload(await server.call_tool("memory_search", {"ids": mid}))["memories"][0]

    assert after["access_count"] > before["access_count"]
