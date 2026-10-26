"""The installed UserPromptSubmit doorway (``user_prompt_recall.py``), run for real.

It applies the length floor in pure stdlib so an acknowledgement never pays to
load the package - except for an agent with a persona, whose short prompt can
still carry board mail and so must reach ``gingugu hook prompt``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from importlib import resources

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="fake gingugu is a sh script")


@pytest.fixture
def shim(tmp_path):
    src = resources.files("gingugu.bootstrap") / "templates" / "user_prompt_recall.py.tmpl"
    script = tmp_path / "user_prompt_recall.py"
    script.write_text(src.read_text())
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "called"
    fake = bin_dir / "gingugu"
    fake.write_text(f'#!/bin/sh\ncat > "{marker}"\necho \'{{"ok": 1}}\'\n')
    fake.chmod(0o755)

    def run(prompt: str, persona: str | None = None) -> tuple[bool, str]:
        env = {k: v for k, v in os.environ.items() if k != "MEMORY_PERSONA"}
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
        if persona is not None:
            env["MEMORY_PERSONA"] = persona
        out = subprocess.run(
            [sys.executable, str(script)],
            input=json.dumps({"prompt": prompt, "session_id": "s"}),
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        assert out.returncode == 0
        return marker.exists(), out.stdout.strip()

    return run


def test_a_short_prompt_without_a_persona_never_loads_the_package(shim):
    called, out = shim("go")
    assert not called and out == ""


def test_a_short_prompt_with_a_persona_goes_through_for_its_mail(shim):
    called, out = shim("go", persona="beepboop")
    assert called and out == '{"ok": 1}'


def test_a_long_prompt_goes_through_either_way(shim):
    called, _ = shim("how does the proxy come back after the brain restarts overnight")
    assert called


def test_a_blank_persona_is_no_persona(shim):
    called, _ = shim("go", persona="  ")
    assert not called
