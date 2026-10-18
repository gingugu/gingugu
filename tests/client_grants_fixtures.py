"""Shared fake process tree and publication helpers for the client-grant suites."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from gingugu import client_grants


@pytest.fixture
def root(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(client_grants, "_root", lambda: tmp_path)
    monkeypatch.delenv("MEMORY_GRANT", raising=False)
    monkeypatch.delenv(client_grants.ROLE_ENV, raising=False)
    return tmp_path


class _Proc:
    """A process in a fake tree: ``others`` says whether it has a child off our chain."""

    def __init__(self, pid: int, age: float = 60.0, others: bool = True) -> None:
        self.pid = pid
        self._created = round(time.time() - age, 2)
        self._others = others

    def create_time(self) -> float:
        return self._created

    def children(self) -> list:
        return [_Proc(-1)] if self._others else []


CLIENT, HOST = _Proc(4242), _Proc(1)


def _hook_under(monkeypatch, *procs) -> None:
    monkeypatch.setattr(client_grants, "ancestors", lambda: list(procs))


def _links(*procs) -> list:
    return [[p.pid, p.create_time()] for p in procs]


def _publication(root: Path, server: int, grant, role=None, via=(CLIENT, HOST)) -> int:
    body = {"grant": grant, "role": role, "created": 1.0, "chain": _links(*via)}
    (root / f"{server}.json").write_text(json.dumps(body))
    return server


@pytest.fixture
def live(monkeypatch):
    """Publishers named here count as running."""
    alive: set[int] = set()
    monkeypatch.setattr(client_grants, "_alive", lambda pid, created: pid in alive)
    return alive


@pytest.fixture
def hook(root, monkeypatch):
    """A hook running directly under CLIENT."""
    _hook_under(monkeypatch, CLIENT, HOST)
