"""X23 spec: what a repo MENTIONS - the names it leans on.

``mentions.extract(files)`` takes ``{repo path: text}`` and returns ``Fact``s of
the kinds in ``model.MENTIONED``. ``mentions.doc_refs(text)`` is the prose reader
shared with the docs map: ``(repo key, path)`` pairs a doc names. Pure: no git,
no disk. The same (kind, key, path, target) is reported once, at its first line.
"""

from __future__ import annotations

import time

import pytest

from gingugu.xray.links import mentions, model
from gingugu.xray.links.model import (
    CI,
    CITE,
    DEP,
    DOC,
    HOST,
    IMAGE,
    NAME,
    REPO_DEP,
    ROUTE,
    SUBMODULE,
)  # fmt: skip


def got(files: dict[str, str], kind: str) -> set[tuple[str, str]]:
    """(key, detail) of every fact of ``kind``."""
    return {(f.key, f.detail) for f in mentions.extract(files) if f.kind == kind}


def keys(files: dict[str, str], kind: str) -> set[str]:
    return {f.key for f in mentions.extract(files) if f.kind == kind}


def one(files: dict[str, str], kind: str, key: str) -> model.Fact:
    found = [f for f in mentions.extract(files) if f.kind == kind and f.key == key]
    assert found, f"no {kind} {key!r} in {mentions.extract(files)}"
    return found[0]


def test_only_mentioned_kinds_come_out():
    files = {"a.py": 'URL = "https://svc-a.acme.io/internal/resolve"\n', "README.md": "x"}
    assert {f.kind for f in mentions.extract(files)} <= set(model.MENTIONED)


# --- CI: repos a job checks out, reuses, clones or acts on -----------------------


def test_checkout_of_another_repo_with_and_without_ref():
    wf = (
        "jobs:\n  t:\n    steps:\n      - uses: actions/checkout@v4\n        with:\n"
        "          repository: Acme/Svc-A\n          ref: main\n          path: a\n"
        "      - uses: actions/checkout@v4\n        with:\n          repository: acme/rig\n"
        "      - uses: actions/checkout@v4\n        with:\n"
        "          repository: ${{ github.repository }}\n"
    )
    found = got({".github/workflows/t.yml": wf}, CI)
    assert ("acme/svc-a", "main") in found and ("acme/rig", "") in found
    assert one({".github/workflows/t.yml": wf}, CI, "acme/svc-a").line == 6


def test_reusable_workflows_and_actions_from_other_repos():
    wf = (
        "jobs:\n  call:\n    uses: acme/shared-ci/.github/workflows/test.yml@v2\n"
        "  s:\n    steps:\n"
        "      - uses: acme/actions-kit/setup@3f2a1b9c0d4e5f60718293a4b5c6d7e8f9012345\n"
        "      - uses: ./.github/actions/local\n      - uses: docker://alpine:3\n"
    )
    found = got({".github/workflows/c.yml": wf}, CI)
    assert ("acme/shared-ci", "v2") in found
    assert ("acme/actions-kit", "3f2a1b9c0d4e5f60718293a4b5c6d7e8f9012345") in found
    assert all("local" not in k and "alpine" not in k for k, _ in found)


def test_git_clone_and_gh_repo_flags_in_ci_and_scripts():
    files = {
        ".gitlab-ci.yml": (
            "test:\n  script:\n    - git clone -b release https://gitlab.acme.io/acme/svc-c.git\n"
        ),
        "scripts/bump.sh": (
            "gh pr create --repo acme/svc-c --title bump\ngh release view -R acme/svc-d\n"
        ),
        "Makefile": "rig:\n\tgit clone git@github.com:acme/test-rig.git rig\n",
    }
    assert got(files, CI) == {
        ("acme/svc-c", "release"), ("acme/svc-c", ""), ("acme/svc-d", ""), ("acme/test-rig", ""),
    }  # fmt: skip


def test_gitlab_multi_project_triggers_and_includes():
    yml = (
        "deploy:\n  trigger:\n    project: group/sub/svc-e\n    branch: main\n"
        "include:\n  - project: group/ci-templates\n    ref: v3\n"
    )
    expected = {("sub/svc-e", "main"), ("group/ci-templates", "v3")}
    assert got({".gitlab-ci.yml": yml}, CI) == expected


def test_a_repository_key_outside_ci_is_not_a_checkout():
    assert keys({"charts/a/values.yaml": "image:\n  repository: acme/svc-a\n"}, CI) == set()


def test_gitmodules_urls_are_submodules():
    gm = '[submodule "vendor/rig"]\n\tpath = vendor/rig\n\turl = https://github.com/acme/test-rig.git\n'
    assert keys({".gitmodules": gm}, SUBMODULE) == {"acme/test-rig"}


# --- dependencies ----------------------------------------------------------------


def test_python_dependencies_everywhere_they_are_declared():
    files = {
        "pyproject.toml": (
            '[project]\nname = "me"\ndependencies = ["Acme_Client>=1.2", "requests"]\n'
            '[project.optional-dependencies]\nrig = ["acme-rig==0.4.1"]\n'
            '[tool.poetry.dependencies]\nwidget-lib = "^2.0"\n'
            '[dependency-groups]\ndev = ["acme.testkit"]\n'
        ),
        "requirements-dev.txt": "# pins\nacme-cli==3.1  # cli\n-r base.txt\n--index-url https://x\n",
    }
    found = got(files, DEP)
    assert ("py:acme-client", ">=1.2") in found and ("py:acme-rig", "==0.4.1") in found
    assert ("py:widget-lib", "^2.0") in found and ("py:acme-testkit", "") in found
    assert ("py:acme-cli", "==3.1") in found and ("py:requests", "") in found
    assert "py:me" not in keys(files, DEP)


def test_npm_dependencies_all_sections():
    pj = (
        '{"name": "me", "dependencies": {"@acme/sdk": "^1.4.0"},\n'
        ' "devDependencies": {"Acme-Rig": "2.0.0"}, "peerDependencies": {"react": ">=18"},\n'
        ' "optionalDependencies": {"acme-x": "*"}}\n'
    )
    found = got({"package.json": pj}, DEP)
    assert {("npm:@acme/sdk", "^1.4.0"), ("npm:acme-rig", "2.0.0"), ("npm:react", ">=18")} <= found
    assert ("npm:acme-x", "*") in found


def test_go_requires_single_and_block():
    gm = (
        "module x\n\nrequire github.com/acme/svc-b v1.4.2\n\nrequire (\n"
        "\tgithub.com/acme/lib v0.3.0 // indirect\n\tgolang.org/x/net v0.20.0\n)\n"
    )
    assert got({"go.mod": gm}, DEP) == {
        ("go:github.com/acme/svc-b", "v1.4.2"), ("go:github.com/acme/lib", "v0.3.0"),
        ("go:golang.org/x/net", "v0.20.0"),
    }  # fmt: skip


def test_git_url_dependencies_name_the_repo():
    files = {
        "requirements.txt": "acme-rig @ git+https://github.com/acme/test-rig.git@v1.2.0\n",
        "package.json": '{"dependencies": {"sdk": "github:acme/svc-sdk#main", "b": "git+https://github.com/acme/b.git#abc123"}}',
    }
    assert got(files, REPO_DEP) == {
        ("acme/test-rig", "v1.2.0"), ("acme/svc-sdk", "main"), ("acme/b", "abc123"),
    }  # fmt: skip


# --- images ------------------------------------------------------------------------


def test_image_references_in_deploy_config():
    files = {
        "k8s/deploy.yaml": (
            "spec:\n  containers:\n    - name: a\n"
            "      image: 123.dkr.ecr.us-east-1.amazonaws.com/team/svc-a:1.4\n"
        ),
        "docker-compose.yml": 'services:\n  b:\n    image: "acme/worker:dev"\n',
        "Dockerfile": "FROM ghcr.io/acme/base-py:3.12 AS build\nFROM build\n",
        "charts/x/values.yaml": "image:\n  repository: team/svc-b\n  tag: v2\n",
        "infra/task.tf": 'container_definitions = jsonencode([{ image = "team/svc-c:latest" }])\n',
    }
    assert keys(files, IMAGE) == {
        "team/svc-a", "acme/worker", "acme/base-py", "team/svc-b", "team/svc-c",
    }  # fmt: skip


# --- hosts and routes ---------------------------------------------------------------


def test_urls_give_a_host_and_a_route():
    src = (
        'BASE = "https://svc-a.acme.io:8443/internal/resolve?x=1"\n'
        'WS = "wss://events.acme.io/stream/v1"\n'
    )
    files = {"src/client.py": src}
    assert keys(files, HOST) == {"svc-a.acme.io", "events.acme.io"}
    assert keys(files, ROUTE) == {"/internal/resolve", "/stream/v1"}
    assert one(files, HOST, "svc-a.acme.io").line == 1


def test_host_valued_config_keys():
    files = {
        "config/app.yaml": 'upstream:\n  host: svc-a.acme.io\n  hostname: "b.acme.io"\n',
        ".env.example": "TOKEN_TAB_HOST=tt.acme.internal\nOTHER=1\n",
        ".mcp.json": '{"mcpServers": {"m": {"url": "https://mcp.acme.io/mcp"}}}',
    }
    assert keys(files, HOST) == {"svc-a.acme.io", "b.acme.io", "tt.acme.internal", "mcp.acme.io"}


def test_reserved_hosts_and_ips_are_not_mentions():
    src = (
        'a = "http://localhost:8000/x/y"\nb = "http://10.0.0.1/a/b"\n'
        'c = "https://api.example.com/q/r"\n'
    )
    assert keys({"a.py": src}, HOST) == set()


def test_route_literals_in_code_concatenated_or_templated():
    src = (
        'resp = httpx.post(f"{base}/internal/resolve", json=b)\n'
        "fetch(`${BASE_URL}/v1/keys/${id}/rotate`)\n"
        "const p = '/jobs/' + id + '/cancel'\n"
        'open("/etc/hosts/allow")\n'
        'x = "not a route"\n'
    )
    found = keys({"src/c.ts": src}, ROUTE)
    assert {"/internal/resolve", "/v1/keys/*/rotate", "/jobs"} <= found
    assert not any(k.startswith("/etc") for k in found)


def test_docs_mention_no_hosts_routes_or_names():
    files = {"README.md": "Call https://svc-a.acme.io/internal/resolve and read `usage-ledger`.\n"}
    assert keys(files, HOST) == keys(files, ROUTE) == keys(files, NAME) == set()


# --- resource-shaped names ------------------------------------------------------------


def test_names_are_resource_shaped_literals_and_values():
    files = {
        "src/db.py": (
            'TABLE = os.environ.get("USAGE_TABLE", "usage-ledger")\nq = "SELECT 1"\nk = "ab"\n'
        ),
        "charts/x/values.yaml": (
            "env:\n  USAGE_TABLE: usage-ledger\n  BUCKET: acme_artifacts.prod\n  MODE: on\n"
        ),
    }
    found = keys(files, NAME)
    assert {"usage-ledger", "acme_artifacts.prod", "USAGE_TABLE"} <= found
    assert not {"SELECT 1", "ab", "on"} & found


@pytest.mark.parametrize(
    "line",
    [
        'OPENAI_API_KEY = "sk-proj-abcdEFGH12345678ijklMNOP"\n',
        "  client_secret: shhh-do-not-tell\n",
        'password = "hunter2-hunter2"\n',
        'AUTH_HEADER = "Bearer-xyz-abc"\n',
        'commit = "3f2a1b9c0d4e5f60718293a4b5c6d7e8f9012345"\n',
        'build = "release-9f8e7d6c5b4a3f2e1d0c"\n',
    ],
)
def test_credential_shaped_values_are_never_names(line):
    # names are cached on disk: a value that may be a credential is never one
    assert keys({"src/settings.py": line, "conf/app.yaml": line}, NAME) == set()


@pytest.mark.parametrize(
    "value",
    ["ghp_abc123", "sk-live-a", "xoxb-1-2", "AKIAEXAMPLE", "eyJhbGciOi.x", "glpat-abc"],
)
def test_well_known_credential_prefixes_are_never_names(value):
    assert keys({"conf/app.yaml": f"value: {value}\n"}, NAME) == set()


def test_an_env_file_gives_hosts_but_never_names():
    files = {".env.example": "UPSTREAM=https://svc-a.acme.io\nTABLE=usage-ledger\n"}
    assert keys(files, NAME) == set() and keys(files, HOST) == {"svc-a.acme.io"}


def test_resource_names_beside_a_secret_line_still_count():
    files = {"conf/app.yaml": "api_key: abc-def-ghi\ntable: usage-ledger\n"}
    assert keys(files, NAME) == {"usage-ledger"}


# --- citations of another repo's source -------------------------------------------------


def test_sha_cites_with_and_without_a_path():
    files = {
        "contracts/request.json": '{"source": "acme/svc-b@4f2a9c1 src/proxy.ts"}\n',
        "NOTES.md": (
            "Shape copied from svc-b@0a1b2c3d4e5f (src/shape.go:12) and acme/rig@deadbeef1.\n"
        ),
    }
    found = {(f.key, f.detail, f.target) for f in mentions.extract(files) if f.kind == CITE}
    assert found == {
        ("acme/svc-b", "4f2a9c1", "src/proxy.ts"), ("svc-b", "0a1b2c3d4e5f", "src/shape.go"),
        ("acme/rig", "deadbeef1", ""),
    }  # fmt: skip


def test_emails_and_versions_are_not_cites():
    files = {"a.md": "mail bob@acme.io or pin pkg@1234567 and lib@1.2.3\n"}
    assert keys(files, CITE) == set()


# --- docs prose ---------------------------------------------------------------------------


def test_doc_refs_prose_owner_qualified_and_links():
    text = (
        "The resolver lives in Acme/Gateway, as guardrails/resolver.py.\n"
        "See acme/gateway/apps/broker/memory.py and `svc-b`/src/x.ts.\n"
        "Also https://github.com/acme/gateway/blob/main/scripts/rig.py#L3\n"
        "Version 0.4.0 of e.g. nothing.\n"
    )
    refs = set(mentions.doc_refs(text))
    assert ("acme/gateway", "guardrails/resolver.py") in refs
    assert ("acme/gateway", "apps/broker/memory.py") in refs
    assert ("acme", "gateway/apps/broker/memory.py") in refs  # the name/path reading
    assert ("acme/gateway", "scripts/rig.py") in refs


@pytest.mark.parametrize(
    "prose",
    [
        "lives in acme/gateway, as guardrails/resolver.py",
        "in `acme/gateway` at `guardrails/resolver.py`",
        "acme/gateway under guardrails/resolver.py",
        "acme/gateway: guardrails/resolver.py",
    ],
)
def test_doc_refs_prose_forms(prose):
    assert ("acme/gateway", "guardrails/resolver.py") in mentions.doc_refs(prose)


def test_doc_facts_come_only_from_docs():
    files = {
        "docs/RUNBOOK.md": "acme/gateway, as guardrails/resolver.py\n",
        "a.py": "# acme/gateway, as x/y.py\n",
    }
    found = [f for f in mentions.extract(files) if f.kind == DOC]
    assert {(f.key, f.target, f.path) for f in found} >= {
        ("acme/gateway", "guardrails/resolver.py", "docs/RUNBOOK.md")
    }
    assert all(f.path == "docs/RUNBOOK.md" for f in found)


# --- cost bounds -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "https://" + "a." * 100000 + "\n",
        '"' + "/a" * 100000 + '"\n',
        "repository: " + "a/" * 100000 + "\n",
        "x@" + "a" * 200000 + "\n",
        "acme/gateway, as " + "a/" * 100000 + "\n",
        "`" * 200000,
        '"' * 200000,
    ],
)
def test_hostile_text_finishes_fast(text):
    start = time.monotonic()
    names = ("a.py", "b.yaml", "c.md", ".github/workflows/x.yml", "package.json")
    files = dict.fromkeys(names, text)
    mentions.extract(files)
    mentions.doc_refs(text)
    assert time.monotonic() - start < 2.0
