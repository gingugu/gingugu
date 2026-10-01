"""Capability pointers, core: validation, the exists check, and the recall lane.

The handler surface (store/update/recall payloads) is covered in
``test_capability_tool.py``; this file pins the pure functions it stands on.
"""

from __future__ import annotations

import json

import pytest

from gingugu import capability
from gingugu.database import Database
from gingugu.models import Confidence, MemoryType, RelationType
from gingugu.namespaces import NamespaceManager
from gingugu.relations import RelationManager
from gingugu.storage import MemoryStore
from tests.test_embeddings import FakeEmbedder


def _meta(block: object) -> str:
    return json.dumps({"capability": block})


# --- check: the type <-> block equivalence ------------------------------------


def test_a_valid_capability_passes():
    assert capability.check("capability", _meta({"run": "uv run jira.py KEY"})) is None
    assert capability.check("capability", _meta({"run": "x", "path": "scripts/x.py"})) is None


@pytest.mark.parametrize(
    "metadata",
    [None, "", json.dumps({"other": 1}), _meta("uv run x"), _meta(["run"])],
)
def test_capability_type_without_a_block_is_refused(metadata):
    assert "needs metadata.capability" in capability.check("capability", metadata)


@pytest.mark.parametrize("block", [{}, {"run": ""}, {"run": "   "}, {"run": 3}, {"path": "x"}])
def test_run_is_required(block):
    assert "run is required" in capability.check("capability", _meta(block))


def test_an_empty_path_is_refused():
    assert "path must be" in capability.check("capability", _meta({"run": "x", "path": " "}))


def test_unknown_fields_are_refused_so_a_typo_cannot_hide():
    error = capability.check("capability", _meta({"run": "x", "cmd": "y"}))
    assert "unknown field" in error and "cmd" in error


def test_the_key_is_reserved_on_every_other_type():
    error = capability.check("workflow", _meta({"run": "x"}))
    assert "reserved for type 'capability'" in error


def test_other_types_with_ordinary_metadata_are_untouched():
    assert capability.check("workflow", None) is None
    assert capability.check("fact", json.dumps({"anything": "else"})) is None


# --- read ----------------------------------------------------------------------


def test_read_returns_the_block_or_none():
    assert capability.read(_meta({"run": "x"})) == {"run": "x"}
    assert capability.read(None) is None
    assert capability.read("not json") is None
    assert capability.read(json.dumps({"capability": "flat"})) is None


# --- describe: the exists check --------------------------------------------------


def test_no_path_means_no_exists_field():
    assert capability.describe({"run": "gh pr list"}, base=None, local=True) == {
        "run": "gh pr list"
    }


def test_absolute_paths_are_checked(tmp_path):
    real = tmp_path / "fetch.py"
    real.write_text("")
    found = capability.describe({"run": "r", "path": str(real)}, base=None, local=True)
    gone = capability.describe(
        {"run": "r", "path": str(tmp_path / "nope.py")}, base=None, local=True
    )
    assert found == {"run": "r", "path": str(real), "exists": True}
    assert gone["exists"] is False


def test_relative_paths_resolve_against_the_namespace_repo(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "fetch.py").write_text("")
    out = capability.describe(
        {"run": "r", "path": "scripts/fetch.py"}, base=str(tmp_path), local=True
    )
    assert out["exists"] is True
    assert out["path"] == "scripts/fetch.py"  # reported as written, not as resolved


def test_a_relative_path_with_no_repo_on_record_is_unknown_not_missing():
    out = capability.describe({"run": "r", "path": "scripts/fetch.py"}, base=None, local=True)
    assert out["exists"] is None


def test_home_relative_paths_expand(tmp_path, monkeypatch):
    # expanduser reads HOME on POSIX and USERPROFILE on Windows (3.8+ ignores
    # HOME there), so point both at the fake home.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "tool").write_text("")
    out = capability.describe({"run": "r", "path": "~/bin/tool"}, base=None, local=True)
    assert out["exists"] is True


def test_a_remote_transport_never_claims_a_file_is_missing(tmp_path):
    """The server's disk is not the caller's: False there would be a lie."""
    out = capability.describe(
        {"run": "r", "path": str(tmp_path / "nope.py")}, base=None, local=False
    )
    assert out["exists"] is None


# --- lane ------------------------------------------------------------------------


@pytest.fixture
def brain(db: Database, namespaces: NamespaceManager):
    return db.conn, MemoryStore(db.conn, embedder=FakeEmbedder()), namespaces


def _cap(store, ns_id, title, content="", **kw):
    return store.create(
        namespace_id=ns_id,
        type=MemoryType.CAPABILITY,
        title=title,
        content=content,
        metadata=_meta({"run": "x"}),
        **kw,
    )


def test_lane_returns_capabilities_above_the_bar_best_first(brain):
    conn, store, namespaces = brain
    ns = namespaces.get_or_create("lane")
    exact = _cap(store, ns.id, "alpha alpha")
    close = _cap(store, ns.id, "alpha alpha beta")
    _cap(store, ns.id, "delta")  # orthogonal: cosine 0
    picked = capability.lane(
        conn, store.embedder, "alpha", namespace_id=ns.id, exclude=set(), bar=0.5
    )
    assert [mid for mid, _ in picked] == [exact.id, close.id]
    assert picked[0][1] == pytest.approx(1.0)


def test_lane_ignores_every_other_type(brain):
    conn, store, namespaces = brain
    ns = namespaces.get_or_create("lane")
    store.create(namespace_id=ns.id, type=MemoryType.WORKFLOW, title="alpha", content="")
    assert capability.lane(conn, store.embedder, "alpha", namespace_id=ns.id, exclude=set()) == []


def test_lane_respects_limit_exclude_and_namespace(brain):
    conn, store, namespaces = brain
    here = namespaces.get_or_create("here")
    there = namespaces.get_or_create("there")
    a = _cap(store, here.id, "alpha")
    b = _cap(store, here.id, "alpha alpha")
    _cap(store, here.id, "alpha alpha alpha")
    elsewhere = _cap(store, there.id, "alpha")

    assert (
        len(capability.lane(conn, store.embedder, "alpha", namespace_id=here.id, exclude=set()))
        == 2
    )
    got = capability.lane(
        conn, store.embedder, "alpha", namespace_id=here.id, exclude={a.id}, limit=5
    )
    assert a.id not in {mid for mid, _ in got} and b.id in {mid for mid, _ in got}
    assert elsewhere.id not in {mid for mid, _ in got}
    both = capability.lane(
        conn, store.embedder, "alpha", namespace_id=[here.id, there.id], exclude=set(), limit=10
    )
    assert elsewhere.id in {mid for mid, _ in both}
    everywhere = capability.lane(
        conn, store.embedder, "alpha", namespace_id=None, exclude=set(), limit=10
    )
    assert len(everywhere) == 4


def test_lane_skips_deprecated_and_superseded(brain):
    conn, store, namespaces = brain
    ns = namespaces.get_or_create("lane")
    old = _cap(store, ns.id, "alpha")
    new = _cap(store, ns.id, "alpha")
    dead = _cap(store, ns.id, "alpha")
    store.update(dead.id, confidence=Confidence.DEPRECATED)
    RelationManager(conn).relate(
        source_id=new.id, target_id=old.id, relation_type=RelationType.SUPERSEDES
    )
    got = {
        mid
        for mid, _ in capability.lane(
            conn, store.embedder, "alpha", namespace_id=ns.id, exclude=set()
        )
    }
    assert got == {new.id}


def test_lane_is_silent_without_an_embedder(brain):
    conn, store, namespaces = brain
    ns = namespaces.get_or_create("lane")
    _cap(store, ns.id, "alpha")
    assert capability.lane(conn, None, "alpha", namespace_id=ns.id, exclude=set()) == []
    assert capability.lane(conn, store.embedder, "  ", namespace_id=ns.id, exclude=set()) == []


def test_the_lane_floor_is_its_own_measurement_not_the_prompt_bar():
    """Short recall queries score lower than whole prompts against the same memory.

    Porting the prompt gate's 0.78 missed most relevant queries when measured;
    see the LANE_BAR comment. This pins that the two stay distinct on purpose.
    """
    from gingugu.recall_gate import DEFAULT_BAR

    assert capability.LANE_BAR == 0.68
    assert capability.LANE_BAR < DEFAULT_BAR
