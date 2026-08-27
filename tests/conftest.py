"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from gingugu.config import Config
from gingugu.credentials import CredentialVault
from gingugu.database import Database
from gingugu.namespaces import NamespaceManager
from gingugu.relations import RelationManager
from gingugu.storage import MemoryStore


class _MemoryKeyring(KeyringBackend):
    """In-memory keyring backend for tests — never touches the OS keychain."""

    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self._store: dict[tuple[str, str], str] = {}

    def set_password(self, servicename: str, username: str, password: str) -> None:
        self._store[(servicename, username)] = password

    def get_password(self, servicename: str, username: str) -> str | None:
        return self._store.get((servicename, username))

    def delete_password(self, servicename: str, username: str) -> None:
        if (servicename, username) not in self._store:
            raise PasswordDeleteError("not found")
        del self._store[(servicename, username)]


@pytest.fixture(autouse=True)
def fake_keyring():
    previous = keyring.get_keyring()
    keyring.set_keyring(_MemoryKeyring())
    yield
    keyring.set_keyring(previous)


@pytest.fixture(autouse=True)
def sandboxed_global_rules(monkeypatch: pytest.MonkeyPatch, tmp_path_factory):
    """Keep the suite out of the developer's real ``~/.claude/CLAUDE.md``.

    ``gingugu init`` manages the user-level rules file as part of the Claude Code
    bootstrap, so every test that calls ``bootstrap.main()`` would otherwise
    append a managed block to the home directory of whoever ran ``pytest``. That
    file is hand-authored and loaded in every session — a test suite must never
    reach it. Autouse, in the same spirit as ``fake_keyring``: the default is
    sandboxed, and a test wanting a specific path passes one explicitly.
    """
    sandbox = tmp_path_factory.mktemp("global-rules") / ".claude" / "CLAUDE.md"
    monkeypatch.setattr(
        "gingugu.bootstrap.global_rules.global_claude_md", lambda: sandbox, raising=True
    )


@pytest.fixture(autouse=True)
def offline_embeddings(monkeypatch: pytest.MonkeyPatch):
    """Keep the suite off the network — the sibling of ``fake_keyring``.

    ``embeddings_enabled`` defaults to True and the fastembed backend
    lazy-loads an ~80MB ONNX model from HuggingFace on first encode, with no
    timeout anywhere in the stack. Any test that builds a real server was
    therefore doing a live download on a cold cache, which is how a CI job
    hung for 10+ minutes on a step that takes under a second everywhere else.

    No test *wants* the real model: ``test_embeddings`` says real loads are
    not exercised, ``test_true_hybrid`` injects its own deterministic
    embedder, and several files had already been patching this env var one at
    a time. This makes that global, so the default is offline and a test that
    needs real vectors has to say so.
    """
    monkeypatch.setenv("MEMORY_EMBEDDINGS_ENABLED", "false")


@pytest.fixture
def config() -> Config:
    from pathlib import Path

    return Config(
        db_path=Path(":memory:"),
        namespace="test-ns",
        namespace_path=None,
        auto_context_limit=10,
        decay_lambda=0.05,
    )


@pytest.fixture
def db(config: Config) -> Database:
    database = Database(config.db_path)
    database.connect()
    yield database
    database.close()


@pytest.fixture
def store(db: Database) -> MemoryStore:
    return MemoryStore(db.conn)


@pytest.fixture
def namespaces(db: Database, config: Config) -> NamespaceManager:
    return NamespaceManager(db.conn, config)


@pytest.fixture
def backdate(db: Database) -> Callable[..., None]:
    """Stamp memories with explicit, well-separated past ``updated_at`` values.

    Any test that asserts an ordering by write time has to *own* its
    timestamps. Storing two memories back to back and trusting them to differ
    is a race against the clock's resolution, and on a coarse one they tie:
    Windows resolved ``datetime.now()`` to 15.6ms until Python 3.13 moved to
    ``GetSystemTimePreciseAsFileTime``, so the same test can pass on every
    developer machine and fail only on that leg of CI.

    Memories are stamped in the order given, one day apart, ending a day in the
    past - so a subsequent real ``store.update()`` lands strictly later than
    every stamp and an edit genuinely leads.
    """

    def _backdate(*memory_ids: str) -> None:
        base = datetime.now(UTC) - timedelta(days=len(memory_ids))
        for offset, memory_id in enumerate(memory_ids):
            db.conn.execute(
                "UPDATE memories SET updated_at = ? WHERE id = ?",
                ((base + timedelta(days=offset)).isoformat(), memory_id),
            )
        db.conn.commit()

    return _backdate


@pytest.fixture
def vault(db: Database) -> CredentialVault:
    return CredentialVault(db.conn)


@pytest.fixture
def relations(db: Database) -> RelationManager:
    return RelationManager(db.conn)
