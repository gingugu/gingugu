"""Hatchling build hook: bundle the built Memory Explorer UI into the wheel.

The UI lives in ``ui/`` and is compiled to ``ui/dist`` (gitignored). We ship
that bundle inside the wheel at ``gingugu/_ui_dist`` so ``gingugu ui`` works for
pip-installed users with no Node. But ``ui/dist`` only exists after
``npm run build`` (done in release.yml before ``uv build``). A *static*
``force-include`` would make every ``uv sync`` / ``uv build`` in a fresh
checkout fail with "Forced include not found" - breaking CI test jobs that
never build the UI. So we add the mapping dynamically, only when the bundle
is actually present.
"""

from __future__ import annotations

import os

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        dist = os.path.join(self.root, "ui", "dist")
        if os.path.isdir(dist):
            build_data["force_include"][dist] = "gingugu/_ui_dist"
        else:
            self.app.display_warning(
                "ui/dist not found - building wheel without the Memory Explorer "
                "UI (run `npm run build` in ui/ to bundle it)."
            )
