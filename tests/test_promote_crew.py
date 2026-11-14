"""``gingugu promote --to-crew``: a crew mate's gold, from its local brain to the crew brain.

The source is the mate's own local brain file - no server to stand up. The
target is the crew link, on the link's scoped token. The locked filter still
applies, and the mate's own self stays home: its persona namespace is refused
as a source, and anything tagged with its persona name (or a ``--skip-tag``)
is held back. A second run promotes nothing twice.
"""

from __future__ import annotations

import json

import pytest

from gingugu.database import Database
from gingugu.models import Confidence, MemoryType
from gingugu.namespaces import NamespaceManager
from gingugu.promote_crew import local_candidates, promote_to_crew
from gingugu.remote import RemoteTarget
from gingugu.serve_tokens import TokenStore
from gingugu.storage import MemoryStore
from tests.proxy_fixtures import make_brain, serving


@pytest.fixture
def local_brain(tmp_path):
    path = tmp_path / "mate" / "memories.db"
    path.parent.mkdir()
    db = Database(path)
    db.connect()
    from gingugu.config import load_config

    nsm = NamespaceManager(db.conn, load_config())
    work, me = nsm.get_or_create("work"), nsm.get_or_create("polly")
    store = MemoryStore(db.conn)
    v = Confidence.VERIFIED

    def add(ns, title, tags=(), confidence=v):
        store.create(
            namespace_id=ns.id,
            type=MemoryType.PATTERN,
            title=title,
            content=f"{title} - the lesson",
            confidence=confidence,
            tags=list(tags),
        )

    add(work, "verify the tag before you ship")
    add(work, "only a hunch", confidence=Confidence.INFERRED)
    add(work, "my own feelings", tags=["polly"])
    add(work, "internal codename", tags=["private"])
    add(work, "session wrap", tags=["session-end"])
    add(me, "who I am")
    db.conn.commit()
    db.close()
    return path


def test_local_candidates_reads_one_namespace_of_the_local_file(local_brain):
    titles = {m["title"] for m in local_candidates(local_brain, "work")}
    assert "verify the tag before you ship" in titles and "who I am" not in titles


def test_unknown_namespace_is_an_error(local_brain):
    with pytest.raises(ValueError, match="no namespace"):
        local_candidates(local_brain, "nope")


async def test_the_persona_namespace_never_leaves(local_brain):
    with pytest.raises(ValueError, match="persona"):
        await promote_to_crew(
            db_path=local_brain,
            source_namespace="polly",
            target=RemoteTarget("http://unused", "env"),
            token="t",
            persona="polly",
            dry_run=True,
        )


@pytest.fixture
async def crew_brain(tmp_path, monkeypatch):
    b = make_brain(tmp_path / "pi", monkeypatch, credentials=False)
    token = TokenStore(tmp_path / "pi" / "serve_tokens.json").add("polly", {"crew": "write"})
    async with serving(b.app()) as served:
        yield RemoteTarget(served.url, "machine"), token


def _pi_parent(tmp_path):
    (tmp_path / "pi").mkdir(exist_ok=True)


@pytest.fixture(autouse=True)
def _pi_dir(tmp_path):
    _pi_parent(tmp_path)


async def test_dry_run_names_only_the_gold_and_writes_nothing(local_brain, crew_brain):
    target, token = crew_brain
    kw = dict(db_path=local_brain, source_namespace="work", target=target, token=token)
    report = await promote_to_crew(**kw, persona="polly", skip_tags=["private"], dry_run=True)
    assert report["would_promote"] == ["verify the tag before you ship"]
    again = await promote_to_crew(**kw, persona="polly", skip_tags=["private"], dry_run=True)
    assert again["already_promoted"] == 0


async def test_promotion_lands_in_crew_stamped_and_only_once(local_brain, crew_brain):
    target, token = crew_brain
    kw = dict(db_path=local_brain, source_namespace="work", target=target, token=token)
    first = await promote_to_crew(**kw, persona="polly", skip_tags=["private"], dry_run=False)
    assert first["promoted"] == 1
    second = await promote_to_crew(**kw, persona="polly", skip_tags=["private"], dry_run=False)
    assert second["promoted"] == 0 and second["already_promoted"] == 1

    from gingugu.promote import _call, _session

    async with _session(f"{target.url}/mcp", token) as s:
        found = await _call(s, "memory_search", {"namespace": "crew", "tags": "promoted"})
    [mem] = found["memories"]
    assert mem["title"] == "verify the tag before you ship" and "promoted" in mem["tags"]

    # The stamp is in the brain itself (search does not show metadata).
    import sqlite3

    conn = sqlite3.connect(str(crew_brain_db(local_brain)))
    (meta,) = conn.execute("SELECT metadata FROM memories WHERE id = ?", (mem["id"],)).fetchone()
    conn.close()
    stamp = json.loads(meta)["promoted_from"]
    assert stamp["namespace"] == "work" and stamp["contributor"] == "polly"


@pytest.mark.parametrize(
    "env, says",
    [
        ({"MEMORY_CREW_URL": "https://crew.test"}, "MEMORY_PERSONA"),
        ({"MEMORY_PERSONA": "polly", "MEMORY_CREW_URL": "off"}, "crew join"),
        (
            {"MEMORY_PERSONA": "polly", "MEMORY_REMOTE_URL": "https://pi.test"},
            "remote already",
        ),
    ],
)
def test_to_crew_says_what_is_missing(monkeypatch, capsys, env, says):
    from gingugu.promote_cli import main

    for key in ("MEMORY_PERSONA", "MEMORY_CREW_URL", "MEMORY_REMOTE_URL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert main(["--to-crew", "--source-ns", "work", "--dry-run"]) == 1
    assert says in capsys.readouterr().err


def crew_brain_db(local_brain):
    return local_brain.parent.parent / "pi" / "brain.db"
