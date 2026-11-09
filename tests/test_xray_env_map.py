"""X7a: env vars as edges - the file reading a variable, and where it is set.

Nothing imports an environment variable: the code reading ``DB_URL`` and the
compose file, k8s manifest, CI workflow, Dockerfile or ``.env.example`` setting it
are invisible to each other in the import graph, and a rename on either side
breaks silently. ``env_map`` reads literal names in known read and set forms,
names only - never a value, and never a real ``.env`` file. Client side only.
"""

from __future__ import annotations

import pytest

from gingugu.xray import env_map
from tests.test_xray_edit_hook import _repo

FILES = {
    "app/settings.py": (
        "import os\n"
        'DB = os.environ["DB_URL"]\n'
        'DEBUG = os.getenv("APP_DEBUG", "0")\n'
        'MODE = os.environ.get("RUN_MODE")\n'
        "name = os.getenv(dynamic)\n"
    ),
    "web/client.ts": (
        "const k = process.env.API_KEY;\n"
        'const h = process.env["API_HOST"];\n'
        "const v = import.meta.env.VITE_FLAG;\n"
    ),
    "cmd/main.go": (
        "package main\n"
        'import "os"\n'
        'func main() { _ = os.Getenv("GO_PORT"); _, _ = os.LookupEnv("GO_HOST") }\n'
    ),
    "docker-compose.yml": (
        "services:\n"
        "  web:\n"
        "    image: x\n"
        "    environment:\n"
        "      - DB_URL=postgres://u:pw@db/x\n"
        "      - UNUSED_SET=1\n"
        "  worker:\n"
        "    environment:\n"
        "      RUN_MODE: worker\n"
    ),
    "k8s/deploy.yaml": (
        "spec:\n"
        "  containers:\n"
        "    - name: app\n"
        "      env:\n"
        "        - name: API_HOST\n"
        "          value: h\n"
        "      image: y\n"
    ),
    ".github/workflows/ci.yml": (
        "jobs:\n"
        "  t:\n"
        "    env:\n"
        '      APP_DEBUG: "1"\n'
        "    steps:\n"
        "      - run: echo hi\n"
    ),
    "Dockerfile": "FROM x\nENV GO_PORT=8080\nARG BUILD_REV\n",
    ".env.example": "API_KEY=changeme\n# COMMENTED=1\nexport VITE_FLAG=1\n",
    ".env": "SECRET_ONLY_IN_DOTENV=hunter2\n",
    ".env.production": "PROD_ONLY=topsecret\n",
    "scripts/run.sh": 'export SCRIPT_SET=1\necho "${SCRIPT_READ:-x}"\n',
    "tests/test_settings.py": 'def t(monkeypatch):\n    monkeypatch.setenv("RUN_MODE", "t")\n',
}


@pytest.fixture
def state(tmp_path):
    return tmp_path / "hook-sessions"


@pytest.fixture
def repo(tmp_path):
    return _repo(tmp_path, FILES)


# --- pure: what one file says --------------------------------------------------


@pytest.mark.parametrize(
    "rel, text, reads, sets",
    [
        ("a.py", 'os.environ["A_B"]\n', [("A_B", 1)], []),
        ("a.py", "x = 1\nos.environ.get('A_B', 'd')\n", [("A_B", 2)], []),
        ("a.py", 'os.getenv("A_B")\n', [("A_B", 1)], []),
        ("a.py", 'environ["A_B"]\n', [("A_B", 1)], []),
        ("a.py", 'os.environ["A_B"] = "1"\n', [], [("A_B", 1)]),
        ("a.py", 'monkeypatch.setenv("A_B", "1")\n', [], [("A_B", 1)]),
        ("a.py", 'os.getenv("lower_case")\nos.getenv(name)\n', [], []),
        ("a.ts", "process.env.A_B\n", [("A_B", 1)], []),
        ("a.js", "process.env['A_B']\n", [("A_B", 1)], []),
        ("a.tsx", "import.meta.env.A_B\n", [("A_B", 1)], []),
        ("a.go", 'os.Getenv("A_B")\n', [("A_B", 1)], []),
        ("a.go", 'os.LookupEnv("A_B")\n', [("A_B", 1)], []),
        (
            "a.sh",
            'export A_B=1\necho "${C_D:-x}" "${E_F:?no}"\n',
            [("C_D", 2), ("E_F", 2)],
            [("A_B", 1)],
        ),  # noqa: E501 fmt: skip
        (
            "Dockerfile",
            "FROM x\nENV A_B=1\nENV C_D 2\nARG E_F\n",
            [],
            [("A_B", 2), ("C_D", 3), ("E_F", 4)],
        ),  # noqa: E501 fmt: skip
        ("build/Dockerfile.dev", "ENV A_B=1\n", [], [("A_B", 1)]),
        (".env.example", "A_B=1\n# C_D=2\n\nexport E_F=3\n", [], [("A_B", 1), ("E_F", 4)]),
        (".env.sample", "A_B=1\n", [], [("A_B", 1)]),
        ("conf/.env.template", "A_B=1\n", [], [("A_B", 1)]),
    ],
)
def test_scan_text_finds_reads_and_sets(rel, text, reads, sets):
    assert env_map.scan_text(rel, text) == (reads, sets)


def test_yaml_env_blocks_in_list_map_and_k8s_forms():
    _, sets = env_map.scan_text("docker-compose.yml", FILES["docker-compose.yml"])
    assert sets == [("DB_URL", 5), ("UNUSED_SET", 6), ("RUN_MODE", 9)]
    _, sets = env_map.scan_text("k8s/deploy.yaml", FILES["k8s/deploy.yaml"])
    assert sets == [("API_HOST", 5)]  # "- name: app" sits outside the env block
    _, sets = env_map.scan_text("ci.yml", FILES[".github/workflows/ci.yml"])
    assert sets == [("APP_DEBUG", 4)]


def test_a_yaml_key_outside_an_env_block_is_not_a_set():
    assert env_map.scan_text("x.yaml", "config:\n  LOG_LEVEL: info\n") == ([], [])


@pytest.mark.parametrize("rel", [".env", ".env.local", ".env.production", "conf/.env", ".envrc"])
def test_a_real_env_file_is_never_read(rel):
    assert env_map.scan_text(rel, "A_B=secret\n") == ([], [])


def test_names_are_upper_snake_case_only():
    reads, _ = env_map.scan_text("a.py", 'os.getenv("A")\nos.getenv("1AB")\nos.getenv("AB-C")\n')
    assert reads == []


# --- the repo map -----------------------------------------------------------------


def test_read_maps_every_read_and_set_in_the_repo(repo, state):
    m = env_map.read(repo, state)
    assert m.reads["DB_URL"] == [("app/settings.py", 2)]
    assert m.sets["DB_URL"] == [("docker-compose.yml", 5)]
    assert m.sets["RUN_MODE"] == [("docker-compose.yml", 9), ("tests/test_settings.py", 2)]
    assert m.reads["GO_HOST"] == [("cmd/main.go", 3)] and "GO_HOST" not in m.sets
    assert m.sets["BUILD_REV"] == [("Dockerfile", 3)] and "BUILD_REV" not in m.reads
    assert m.sets["API_KEY"] == [(".env.example", 1)]


def test_a_real_env_file_and_its_values_never_reach_the_map_or_cache(repo, state):
    m = env_map.read(repo, state)
    assert "SECRET_ONLY_IN_DOTENV" not in m.sets and "PROD_ONLY" not in m.sets
    cached = "".join(p.read_text() for p in state.glob("xray-env-*.json"))
    for leaked in ("hunter2", "topsecret", "changeme", "postgres://", "SECRET_ONLY"):
        assert leaked not in cached


def test_an_unchanged_file_is_not_rescanned(repo, state, monkeypatch):
    env_map.read(repo, state)
    seen = []
    real = env_map.scan_text
    monkeypatch.setattr(env_map, "scan_text", lambda rel, text: seen.append(rel) or real(rel, text))
    (repo / "app/settings.py").write_text('import os\nos.getenv("NEW_ONE")\n')
    m = env_map.read(repo, state)
    assert seen == ["app/settings.py"]
    assert m.reads["NEW_ONE"] == [("app/settings.py", 2)] and "DB_URL" not in m.reads


def test_a_file_past_the_byte_cap_is_skipped(repo, state, monkeypatch):
    monkeypatch.setattr(env_map, "MAX_FILE_BYTES", 16)
    m = env_map.read(repo, state)
    assert "DB_URL" not in m.reads


def test_a_corrupt_cache_is_rebuilt(repo, state):
    env_map.read(repo, state)
    for p in state.glob("xray-env-*.json"):
        p.write_text("{not json")
    assert env_map.read(repo, state).reads["DB_URL"]


def test_the_switch(monkeypatch):
    monkeypatch.setenv("MEMORY_XRAY_ENV", "off")
    assert env_map.enabled() is False
    monkeypatch.setenv("MEMORY_XRAY_ENV", "on")
    assert env_map.enabled() is True


# --- review fixes: linear scan, a budget that converges, capped cache, case-blind gate ----


def test_a_file_full_of_reads_scans_in_linear_time():
    import time

    text = 'os.getenv("AB")\n' * 15000
    t = time.monotonic()
    reads, _ = env_map.scan_text("big.py", text)
    assert time.monotonic() - t < 1.0 and len(reads) == 15000


def test_past_the_budget_a_cold_read_reaches_nothing_and_a_full_one_everything(repo, state):
    cold = env_map.read(repo, state, budget_s=0)
    assert cold.reads == {} and cold.complete is False
    full = env_map.read(repo, state)
    assert full.reads["DB_URL"] and full.complete is True


def test_past_the_budget_a_file_keeps_its_cached_entry(repo, state):
    env_map.read(repo, state)
    warm = env_map.read(repo, state, budget_s=0)
    assert warm.reads["DB_URL"] == [("app/settings.py", 2)]
    assert warm.complete is True  # every file answered, if from the cache


def test_the_hooks_stay_silent_on_a_partial_map(repo, state, monkeypatch):
    from gingugu.xray import env_hook

    monkeypatch.setattr(env_hook, "BUDGET_S", 0)
    assert env_hook.file_line(repo, "cmd/main.go", state) is None  # not "set nowhere"


def test_a_cached_list_past_the_cap_is_rescanned(repo, state, monkeypatch):
    env_map.read(repo, state)
    monkeypatch.setattr(env_map, "MAX_NAMES", 0)
    (cache,) = state.glob("xray-env-*.json")
    old, _ = env_map._load(cache)
    assert "app/settings.py" not in old  # 3 reads > a cap of 0: dropped, rescanned


@pytest.mark.parametrize("rel", [".ENV", ".Env.local", "conf/.ENV.yml", ".ENVRC"])
def test_the_dotenv_gate_ignores_case(rel):
    assert env_map.kind(rel) is None
