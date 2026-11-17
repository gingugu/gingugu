"""The suite never inherits the runner's persona or crew link (conftest)."""

from __future__ import annotations

import os


def test_identity_env_is_cleared_for_every_test():
    for name in ("MEMORY_PERSONA", "MEMORY_CREW", "MEMORY_CREW_URL"):
        assert name not in os.environ, name
