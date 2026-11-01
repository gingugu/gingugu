"""X3 scar tissue, the brain's half: which memories name a file.

Derived at read time, never stored: the brain FTS-searches one project
namespace for a file's basename and sends back, per hit, the memory's id,
type, title, age and every path-like token it names. It never sees the
checkout; the client resolves those tokens against its own files and scores
them. Only the kinds of memory that record a cost are asked for - bugs,
decisions, architecture, facts and patterns - since workflow and context notes
name files in passing.
"""

from __future__ import annotations

import pytest

from gingugu.models import Confidence, MemoryType
from gingugu.namespaces import NamespaceManager
from gingugu.xray import scar_query

# --- path_tokens ----------------------------------------------------------------


def test_path_tokens_finds_full_paths_and_bare_names():
    text = "Fixed in src/gingugu/storage.py; see also store.py and docs/architecture.md."
    assert scar_query.path_tokens(text) == [
        "src/gingugu/storage.py",
        "store.py",
        "docs/architecture.md",
    ]


@pytest.mark.parametrize(
    "noise",
    ["0.40.0", "v0.29.0", "e.g.", "host.example.local", "i.e. this", "gingugu.com", "1.5x faster"],
)
def test_path_tokens_ignores_versions_hosts_and_prose(noise):
    assert scar_query.path_tokens(f"shipped {noise} today") == []


def test_path_tokens_strips_markup_and_line_suffixes():
    text = "`xray/rank.py` and [a](src/gingugu/xray/brief.py#L12) and ./tests/test_a.py:42"
    assert scar_query.path_tokens(text) == [
        "xray/rank.py",
        "src/gingugu/xray/brief.py",
        "tests/test_a.py",
    ]


def test_path_tokens_are_deduplicated_in_first_seen_order():
    assert scar_query.path_tokens("b.py a.py b.py a.py") == ["b.py", "a.py"]


def test_path_tokens_are_capped():
    text = " ".join(f"m{i}.py" for i in range(scar_query.MAX_PATHS + 10))
    assert len(scar_query.path_tokens(text)) == scar_query.MAX_PATHS


@pytest.mark.parametrize("ext", ["py", "ts", "tsx", "js", "go", "md", "toml", "json", "yml"])
def test_path_tokens_know_the_usual_extensions(ext):
    assert scar_query.path_tokens(f"see pkg/thing.{ext} now") == [f"pkg/thing.{ext}"]


# --- scars ----------------------------------------------------------------------


@pytest.fixture
def brain(db, config, store):
    nsm = NamespaceManager(db.conn, config)
    proj = nsm.get_or_create("proj")
    other = nsm.get_or_create("other")

    def add(title, content, type=MemoryType.BUG, ns=proj, confidence=Confidence.VERIFIED):
        return store.create(
            namespace_id=ns.id, type=type, title=title, content=content, confidence=confidence
        ).id

    db.conn.commit()
    return db.conn, add, other


def test_a_bug_naming_the_file_comes_back_with_its_paths(brain):
    conn, add, _ = brain
    mid = add("Crash in the store", "The fix is in src/pkg/storage.py and tests/test_storage.py.")
    (hit,) = scar_query.scars(conn, "proj", "storage.py")
    assert hit["id"] == mid
    assert hit["type"] == "bug"
    assert hit["title"] == "Crash in the store"
    assert hit["paths"] == ["src/pkg/storage.py", "tests/test_storage.py"]
    assert isinstance(hit["created_at"], str) and hit["created_at"]


@pytest.mark.parametrize(
    "mtype",
    [
        MemoryType.BUG,
        MemoryType.DECISION,
        MemoryType.ARCHITECTURE,
        MemoryType.FACT,
        MemoryType.PATTERN,
    ],
)
def test_the_kinds_that_record_a_cost_are_asked_for(brain, mtype):
    conn, add, _ = brain
    add("t", "about storage.py", type=mtype)
    assert len(scar_query.scars(conn, "proj", "storage.py")) == 1


@pytest.mark.parametrize("mtype", [MemoryType.WORKFLOW, MemoryType.CONTEXT, MemoryType.PREFERENCE])
def test_notes_that_name_files_in_passing_are_left_out(brain, mtype):
    conn, add, _ = brain
    add("t", "session touched storage.py", type=mtype)
    assert scar_query.scars(conn, "proj", "storage.py") == []


def test_only_the_namespace_asked_for(brain):
    conn, add, other = brain
    add("elsewhere", "storage.py broke", ns=other)
    assert scar_query.scars(conn, "proj", "storage.py") == []


def test_a_deprecated_memory_is_no_scar(brain):
    conn, add, _ = brain
    add("wrong", "storage.py was blamed", confidence=Confidence.DEPRECATED)
    assert scar_query.scars(conn, "proj", "storage.py") == []


def test_prose_that_only_reads_like_the_name_is_no_hit(brain):
    conn, add, _ = brain
    add("prose", "the storage py layer")  # FTS phrase matches; no token is storage.py
    assert scar_query.scars(conn, "proj", "storage.py") == []


def test_another_file_with_a_longer_name_is_no_hit(brain):
    conn, add, _ = brain
    add("other", "see storage_extra.py")
    assert scar_query.scars(conn, "proj", "storage.py") == []


def test_a_path_ending_in_the_name_is_a_hit(brain):
    conn, add, _ = brain
    add("deep", "see a/b/storage.py")
    hits = scar_query.scars(conn, "proj", "storage.py")
    assert [h["paths"] for h in hits] == [["a/b/storage.py"]]


def test_newest_first_and_capped(brain, monkeypatch):
    conn, add, _ = brain
    monkeypatch.setattr(scar_query, "MAX_HITS", 3)
    ids = [add(f"m{i}", f"storage.py #{i}") for i in range(5)]
    for i, mid in enumerate(ids):  # own the timestamps: back-to-back writes can tie
        conn.execute(
            "UPDATE memories SET created_at = ? WHERE id = ?", (f"2026-01-0{i + 1}T00:00:00", mid)
        )
    hits = scar_query.scars(conn, "proj", "storage.py")
    assert [h["id"] for h in hits] == [ids[4], ids[3], ids[2]]


def test_a_long_title_is_cut(brain):
    conn, add, _ = brain
    add("x" * 500, "storage.py")
    (hit,) = scar_query.scars(conn, "proj", "storage.py")
    assert len(hit["title"]) == scar_query.TITLE_CHARS


@pytest.mark.parametrize(
    "bad", ["", "storage", '"x".py', "a b.py", "../x.py", "x/y.py", "*.py", "a" * 200 + ".py"]
)
def test_a_name_that_is_not_a_plain_file_name_is_refused_quietly(brain, bad):
    conn, add, _ = brain
    add("t", "storage.py")
    assert scar_query.valid_basename(bad) is False
    assert scar_query.scars(conn, "proj", bad) == []


def test_an_unknown_namespace_is_empty_not_an_error(brain):
    conn, _, _ = brain
    assert scar_query.scars(conn, "nope", "storage.py") == []


def test_an_overlong_token_is_no_path():
    long = "a/" * 200 + "x.py"
    assert scar_query.path_tokens(f"see {long} and b.py") == ["b.py"]
