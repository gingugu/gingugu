"""A three-language repo for the X16 incremental-refresh tests, and its mutations.

Every language takes part, along with what makes each hard to cache: a manifest
that changes resolution (pyproject scripts, tsconfig paths, go.mod), a file
that does not parse, a ``__main__`` guard, a test file whose role comes from
its path, and imports guarded by TYPE_CHECKING or made dynamically.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

FILES: dict[str, str] = {
    "pyproject.toml": '[project]\nname = "x"\n[project.scripts]\nx = "app.cli:main"\n',
    "app/__init__.py": "",
    "app/cli.py": "from app import core\nimport importlib\nimportlib.import_module('x')\n",
    "app/core.py": (
        "from . import util\nfrom typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n    from app import cli\n"
    ),
    "app/util.py": "def f():\n    import app.core\n",
    "app/run.py": "import app.util\nif __name__ == '__main__':\n    pass\n",
    "app/broken.py": "def (:\n",
    "tests/test_core.py": "from app.core import util\n",
    "web/tsconfig.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@lib/*": ["src/lib/*"]}}}',
    "web/package.json": '{"name": "web", "main": "src/index.ts"}',
    "web/src/index.ts": 'import { a } from "@lib/a";\nimport "./side";\nconst m = import("./lazy");\n',
    "web/src/lib/a.ts": "export const a = 1;\n",
    "web/src/side.ts": "export const side = 2;\n",
    "web/src/lazy.ts": "export default 1;\n",
    "web/src/broken.ts": "import { from ;\n",
    "web/src/a.test.ts": 'import { a } from "./lib/a";\n',
    "svc/go.mod": "module example.com/svc\n\ngo 1.22\n",
    "svc/main.go": 'package main\n\nimport "example.com/svc/store"\n\nfunc main() { store.Get() }\n',
    "svc/store/store.go": "package store\n\nfunc Get() {}\n",
    "svc/store/db.go": "package store\n\nvar db = 1\n",
    "svc/store/bad.go": "package store\n\nfunc {\n",
    "svc/store/store_test.go": 'package store\n\nimport "testing"\n\nfunc TestX(t *testing.T) {}\n',
}


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def make_repo(tmp_path: Path, files: dict[str, str] = FILES) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    for rel, text in files.items():
        write(repo, rel, text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode())  # write_text turns \n into \r\n on Windows


def rename(repo: Path, src: str, dst: str) -> None:
    (repo / dst).parent.mkdir(parents=True, exist_ok=True)
    (repo / src).rename(repo / dst)


# name -> (mutation, the source files whose bytes are new and must be parsed)
MUTATIONS = {
    "edit_python": (lambda r: write(r, "app/util.py", "import app.cli\n"), {"app/util.py"}),
    "edit_ts": (lambda r: write(r, "web/src/side.ts", 'import "./lazy";\n'), {"web/src/side.ts"}),
    "edit_go": (
        lambda r: write(r, "svc/store/db.go", 'package store\n\nimport "example.com/svc"\n'),
        {"svc/store/db.go"},
    ),
    "add": (lambda r: write(r, "web/src/new.ts", 'import "./side";\n'), {"web/src/new.ts"}),
    "delete": (lambda r: (r / "svc/store/db.go").unlink(), set()),
    "rename_same_dir": (lambda r: rename(r, "app/util.py", "app/helpers.py"), set()),
    "rename_into_tests": (lambda r: rename(r, "app/run.py", "tests/run.py"), set()),
    "rename_ts": (lambda r: rename(r, "web/src/side.ts", "web/src/lib/side.ts"), set()),
    "manifest_tsconfig": (
        lambda r: write(
            r,
            "web/tsconfig.json",
            '{"compilerOptions": {"baseUrl": ".", "paths": {"@lib/*": ["src/*"]}}}',
        ),
        set(),
    ),
    "manifest_gomod": (lambda r: write(r, "svc/go.mod", "module example.com/other\n"), set()),
    "manifest_pyproject": (
        lambda r: write(r, "pyproject.toml", '[project]\nname = "x"\n[project.scripts]\n'),
        set(),
    ),
}
