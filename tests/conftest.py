"""Shared pytest fixtures."""

from __future__ import annotations

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
def vault(db: Database) -> CredentialVault:
    return CredentialVault(db.conn)


@pytest.fixture
def relations(db: Database) -> RelationManager:
    return RelationManager(db.conn)
