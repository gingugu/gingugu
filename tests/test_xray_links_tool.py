"""X23 ``code_xray`` action ``links``, end to end on a small service ecosystem.

Four checkouts wired the way real services are, with nothing declared by hand:

- ``acme/gateway`` serves ``gateway.acme.io`` (Ingress) and ``POST /internal/resolve``
  in ``guardrails/resolver.py``, which imports ``guardrails/policy.py``;
- ``acme/ledger`` calls that route through a base URL set in its chart, checks the
  gateway out in CI to run its rig, documents the resolver in prose
  ("lives in acme/gateway, as guardrails/resolver.py" - the field repro), and
  publishes ``@acme/ledger``;
- ``acme/sidecar`` carries a request-shape contract citing ``acme/gateway@<sha>``;
- ``acme/portal`` pins ``@acme/ledger``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gingugu.xray.links import tool
from tests.test_xray_edit_hook import _git
from tests.test_xray_links_index import _checkout

GATEWAY = {
    "guardrails/__init__.py": "",
    "guardrails/policy.py": "LIMIT = 3\n",
    "guardrails/resolver.py": (
        "from fastapi import FastAPI\n\nfrom guardrails import policy\n\napp = FastAPI()\n\n"
        '@app.post("/internal/resolve")\ndef resolve():\n    return policy.LIMIT\n'
    ),
    "guardrails/other.py": "X = 1\n",
    "k8s/ingress.yaml": "kind: Ingress\nspec:\n  rules:\n    - host: gateway.acme.io\n",
}
ledger = {
    "package.json": '{"name": "@acme/ledger", "version": "1.0.0"}\n',
    "src/hook.ts": "export const r = () => fetch(`${process.env.GATEWAY_URL}/internal/resolve`)\n",
    "charts/tt/values.yaml": "env:\n  GATEWAY_URL: https://gateway.acme.io\n",
    ".github/workflows/rig.yml": (
        "jobs:\n  rig:\n    steps:\n      - uses: actions/checkout@v4\n        with:\n"
        "          repository: acme/gateway\n          ref: main\n"
    ),
    "docs/gateway-hook.md": "The resolver lives in acme/gateway, as guardrails/resolver.py.\n",
}
PORTAL = {"package.json": '{"name": "portal", "dependencies": {"@acme/ledger": "^1.0.0"}}\n'}


def _sha(repo: Path) -> str:
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
    return out.stdout.strip()


@pytest.fixture
def eco(tmp_path, monkeypatch):
    base = tmp_path / "GIT"
    gw = _checkout(base, "gateway", GATEWAY, "git@github.com:acme/gateway.git")
    sha = _sha(gw)
    _checkout(base, "ledger", ledger, "https://github.com/acme/ledger.git")
    sidecar = {
        "contracts/shape.json": f'{{"source": "acme/gateway@{sha} guardrails/resolver.py"}}\n'
    }
    _checkout(base, "sidecar", sidecar, "https://github.com/acme/sidecar.git")
    _checkout(base, "portal", PORTAL, "https://github.com/acme/portal.git")
    monkeypatch.setenv("MEMORY_XRAY_REPOS", str(base))
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.delenv("MEMORY_XRAY_LINKS", raising=False)
    return gw


def _in(out: dict) -> set[tuple]:
    return {(r["from"], r["file"], r["target"], r["kind"]) for r in out["leaning_on_this"]}


def test_whole_repo_shows_every_kind_of_link_in_and_none_out(eco):
    out = tool.answer(str(eco))
    assert out["ok"] is True and out["repo"] == "acme/gateway"
    assert out["read"] == {"repos": 4, "partial": {}, "unscanned": []}
    assert _in(out) == {
        ("acme/ledger", "src/hook.ts", "guardrails/resolver.py", "runtime"),
        ("acme/ledger", "charts/tt/values.yaml", "k8s/ingress.yaml", "runtime"),
        ("acme/ledger", ".github/workflows/rig.yml", None, "ci"),
        ("acme/ledger", "docs/gateway-hook.md", "guardrails/resolver.py", "docs"),
        ("acme/sidecar", "contracts/shape.json", "guardrails/resolver.py", "cite"),
    }
    assert all(r["certain"] for r in out["leaning_on_this"])
    ci = next(r for r in out["leaning_on_this"] if r["kind"] == "ci")
    assert ci["detail"] == "main" and "unpinned" in ci["flags"]
    assert out["this_leans_on"] == [] and out["this_leans_on_total"] == 0
    assert out["text"].startswith("GINGUGU X-RAY LINKS: acme/gateway - 4 checkouts read")
    assert "acme/ledger src/hook.ts:1 -> guardrails/resolver.py [runtime" in out["text"]


def test_a_file_reaches_outside_through_its_importers_and_ripples_on(eco):
    out = tool.answer(str(eco), files="guardrails/policy.py")
    # resolver.py imports policy.py, so everything leaning on resolver.py leans on policy.py
    assert ("acme/ledger", "src/hook.ts", "guardrails/resolver.py", "runtime") in _in(out)
    assert ("acme/ledger", ".github/workflows/rig.yml", None, "ci") in _in(out)
    hops = {(r["repo"], r["hop"]) for r in out["ripple"]}
    # portal pins what ledger ships, and the change reached ledger
    assert hops == {("acme/ledger", 1), ("acme/sidecar", 1), ("acme/portal", 2)}
    assert "Ripple across repos:" in out["text"] and "hop 2: acme/portal" in out["text"]


def test_a_file_nothing_outside_leans_on_has_only_whole_repo_links(eco):
    out = tool.answer(str(eco), files="guardrails/other.py")
    assert _in(out) == {("acme/ledger", ".github/workflows/rig.yml", None, "ci")}


def test_the_other_side_sees_what_it_leans_on(eco, tmp_path):
    out = tool.answer(str(tmp_path / "GIT/ledger"))
    outs = {(r["to"], r["kind"]) for r in out["this_leans_on"]}
    assert {("acme/gateway", "runtime"), ("acme/gateway", "ci"), ("acme/gateway", "docs")} <= outs
    assert ("acme/portal", "pin") in {(r["from"], r["kind"]) for r in out["leaning_on_this"]}


def test_a_cite_whose_file_changed_since_its_sha_is_flagged_drifted(eco):
    (eco / "guardrails/resolver.py").write_text(GATEWAY["guardrails/resolver.py"] + "# v2\n")
    _git(eco, "commit", "-qam", "v2")
    out = tool.answer(str(eco))
    cite = next(r for r in out["leaning_on_this"] if r["kind"] == "cite")
    assert "drifted" in cite["flags"]


@pytest.mark.parametrize("sha", ["--output=/tmp/pwned", "-p", "HEAD", "abc", "g" * 40])
def test_a_cite_sha_that_is_not_hex_never_reaches_git(eco, sha, monkeypatch):
    from gingugu.xray import scan
    from gingugu.xray.links import index, join, model

    e = index.read(eco, eco.parent.parent / "state", budget_s=10)
    lk = model.Link("acme/sidecar", "c.json", 1, "acme/gateway", "guardrails/resolver.py",
                    "cite", "acme/gateway", sha)  # fmt: skip
    called = []
    monkeypatch.setattr(scan, "git", lambda *a, **k: called.append(a))
    assert tool._drift(e, lk, 1.0) is None and called == []
    assert join  # imported for the Link's home


def test_a_cite_of_a_path_git_never_listed_never_reaches_git(eco, monkeypatch):
    from gingugu.xray import scan
    from gingugu.xray.links import index, model

    e = index.read(eco, eco.parent.parent / "state", budget_s=10)
    lk = model.Link("acme/sidecar", "c.json", 1, "acme/gateway", "-rf/../../etc/passwd",
                    "cite", "acme/gateway", "a" * 40)  # fmt: skip
    called = []
    monkeypatch.setattr(scan, "git", lambda *a, **k: called.append(a))
    assert tool._drift(e, lk, 1.0) is None and called == []


def test_links_can_be_turned_off(eco, monkeypatch):
    monkeypatch.setenv("MEMORY_XRAY_LINKS", "off")
    with pytest.raises(ValueError, match="MEMORY_XRAY_LINKS"):
        tool.answer(str(eco))
