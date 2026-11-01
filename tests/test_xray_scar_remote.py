"""X3 scar tissue over a remote brain: ``/hook/scars`` and its client.

The route is an owner-only hook route like ``/hook/tripwires``: the machine's
own hooks ask which memories in one project namespace name a file, by basename.
The brain answers from its own read-only connection; the client validates the
reply and treats any failure as no scars, never as a fall back to a local copy.
"""

from __future__ import annotations

import pytest

from gingugu.grants import Grant
from gingugu.models import MemoryType
from tests.hook_remote_fixtures import URL
from tests.test_hook_remote_server import _post, brain, served  # noqa: F401 - fixtures

HIT = {
    "id": "m-1",
    "type": "bug",
    "title": "Crash in the store",
    "created_at": "2026-10-01T00:00:00",
    "paths": ["src/pkg/storage.py"],
}


@pytest.fixture
def scarred(brain):  # noqa: F811 - the imported fixture, extended
    from gingugu.config import load_config
    from gingugu.database import Database
    from gingugu.namespaces import NamespaceManager
    from gingugu.storage import MemoryStore
    from tests.test_embeddings import FakeEmbedder

    database = Database(load_config().db_path)
    conn = database.connect()
    ns = NamespaceManager(conn, load_config()).get_or_create("proj")
    mem = MemoryStore(conn, embedder=FakeEmbedder()).create(
        namespace_id=ns.id,
        type=MemoryType.BUG,
        title="Crash in the store",
        content="Fixed in src/pkg/storage.py.",
    )
    conn.commit()
    database.close()
    brain.scar_id = mem.id
    return brain


# --- the route --------------------------------------------------------------------


async def test_the_brain_names_the_memories_about_a_file(scarred, served):  # noqa: F811
    body = {"namespace": "proj", "basename": "storage.py"}
    r = await _post(served, "/hook/scars", body, scarred.machine)
    assert r.status_code == 200
    (hit,) = r.json()["scars"]
    assert hit["id"] == scarred.scar_id
    assert hit["paths"] == ["src/pkg/storage.py"]
    r = await _post(served, "/hook/scars", {**body, "namespace": "elsewhere"}, scarred.machine)
    assert r.json() == {"scars": []}


async def test_only_an_owner_token_may_ask(brain, served):  # noqa: F811
    derived = brain.derived.mint(Grant("derived:x", {"crow": "write"}, derived=True), 60)
    r = await _post(served, "/hook/scars", {}, derived)
    assert r.status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"namespace": "proj"},
        {"basename": "storage.py"},
        {"namespace": "My Repo", "basename": "storage.py"},
        {"namespace": "proj", "basename": "../etc/passwd"},
        {"namespace": "proj", "basename": 7},
        {"namespace": ["proj"], "basename": "storage.py"},
    ],
)
async def test_a_bad_body_is_a_400(brain, served, body):  # noqa: F811
    r = await _post(served, "/hook/scars", body, brain.machine)
    assert r.status_code == 400


# --- the client ---------------------------------------------------------------------


@pytest.fixture
def target():
    from gingugu.remote import RemoteTarget

    return RemoteTarget(URL, "env")


def test_the_client_asks_for_one_namespace_and_one_name(remote, target):
    from gingugu.hook_remote_flows import remote_scars

    _, install = remote
    brain = install({"/hook/scars": {"scars": [HIT]}})
    assert remote_scars(target, "proj", "storage.py") == [HIT]
    assert brain.calls == [("/hook/scars", {"namespace": "proj", "basename": "storage.py"})]


@pytest.mark.parametrize(
    "reply",
    [
        None,
        {},
        {"scars": "x"},
        {"scars": [{"id": "m-1"}]},
        {"scars": [{**HIT, "paths": "src/pkg/storage.py"}]},
        {"scars": [{**HIT, "title": 5}]},
        {"scars": [{**HIT, "paths": [1]}]},
    ],
)
def test_an_unanswered_or_malformed_reply_is_none(remote, target, reply):
    from gingugu.hook_remote_flows import remote_scars

    _, install = remote
    install({"/hook/scars": reply})
    assert remote_scars(target, "proj", "storage.py") is None


def test_a_name_the_brain_would_refuse_is_never_sent(remote, target):
    from gingugu.hook_remote_flows import remote_scars

    _, install = remote
    brain = install({"/hook/scars": {"scars": []}})
    assert remote_scars(target, "My Repo", "storage.py") is None
    assert remote_scars(target, "proj", "a b.py") is None
    assert brain.calls == []
