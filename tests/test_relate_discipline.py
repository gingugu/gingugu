"""Guards on the *guidance* surfaces that drive relation-writing behaviour.

Tool descriptions are not documentation for humans — for an MCP server they are
the contract the calling model reads on every single turn, and the bootstrap
templates are guidance shipped verbatim into users' repos. Both are product.

This file exists because a single line of prose caused a measurable retrieval
defect. ``AGENTS.md`` used to describe ``related_to`` as "most common — use
liberally"; the result, measured 2026-08-04 on a real 909-memory brain, was
1369 edges of which 69% were ``related_to``. Those edges encode only topical
adjacency, which hybrid search already derives for free, and because
``dampened_neighbour_ids`` was then blind to relation type they competed for
(and won) the per-seed budget of 3 against edges carrying real signal.

The traversal now weights by type, so those edges lose the slot instead of
taking it. That fixes the retrieval damage, not the framing: an edge nobody can
name a directional fact for is still a wasted write, and precise edges still
compete with each other for a budget of 3. The guidance below must keep saying
so.

So these tests pin the framing, not just the plumbing: a regression here is a
regression in what the brain records.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gingugu.bootstrap import CLIENT_RULES_FILES, main
from gingugu.config import Config
from gingugu.database import Database
from gingugu.handlers import ServerContext
from gingugu.handlers import memory as memory_handlers
from gingugu.handlers import relations as relations_handlers
from gingugu.namespaces import NamespaceManager
from gingugu.storage import MemoryStore

# Vocabulary that pushes edge VOLUME over edge QUALITY. Every one of these was
# live in a guidance surface before 2026-08-04.
VOLUME_LANGUAGE = (
    "use liberally",
    "aggressively",
    "nothing floats loose",
    "to their cluster",
    "most common",
)

DIRECTIONAL_TYPES = ("supersedes", "contradicts", "caused_by", "parent_of", "child_of")

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "gingugu" / "bootstrap" / "templates"


class _CaptureMCP:
    """Minimal stand-in for FastMCP that records each tool's description.

    Registering against this instead of a real server keeps the assertion on
    the shipped docstring itself and independent of MCP SDK internals.
    """

    def __init__(self) -> None:
        self.tools: dict[str, str] = {}

    def tool(self, *_args, **_kwargs):
        def decorator(fn):
            self.tools[fn.__name__] = fn.__doc__ or ""
            return fn

        return decorator


@pytest.fixture
def descriptions(db: Database, store: MemoryStore, namespaces: NamespaceManager, config: Config):
    """Tool descriptions exactly as the calling model receives them."""
    ctx = ServerContext(config=config, store=store, namespaces=namespaces, conn=db.conn)
    capture = _CaptureMCP()
    relations_handlers.register(capture, ctx)
    memory_handlers.register(capture, ctx)
    return capture.tools


# --- the tool surface the model reads every turn ------------------------------


def test_memory_relate_description_ranks_related_to_last(descriptions):
    doc = descriptions["memory_relate"]

    # related_to must be named a fallback, and must appear only AFTER every
    # directional type — ordering in the prose is the priority signal the model
    # actually acts on.
    assert "fallback" in doc.lower()
    last_directional = max(doc.index(t) for t in DIRECTIONAL_TYPES)
    assert doc.rindex("related_to") > last_directional

    # And it must say why, or the rule reads as arbitrary and gets ignored.
    assert "cannot infer" in doc or "for free" in doc


def test_memory_relate_description_states_the_traversal_weights_by_type(descriptions):
    """The budget mechanism is the whole reason restraint pays off.

    It must describe what the traversal actually does. Claiming type-blindness
    after ``dampened_neighbour_ids`` started weighting by type would tell the
    calling model a vague edge *steals* a slot, when in fact it *forfeits* one -
    a different, and weaker, reason not to write it. The honest reason is that
    the edge buys nothing and the budget is still only 3.
    """
    doc = descriptions["memory_relate"].lower()
    assert "3 neighbours per" in doc
    assert "weights by relation type" in doc
    assert "not weight by relation type" not in doc and "does not weight by type" not in doc


def test_hint_descriptions_frame_candidates_as_examine_not_link(descriptions):
    """``suggested_relations`` must not read as an instruction to link."""
    for tool in ("memory_store", "memory_update"):
        doc = descriptions[tool]
        assert "suggested_relations" in doc
        assert "nudge to call" not in doc
        assert "grow the knowledge graph" not in doc
    assert "link nothing" in descriptions["memory_store"]


@pytest.mark.parametrize("tool", ["memory_relate", "memory_store", "memory_update"])
def test_tool_descriptions_carry_no_volume_language(descriptions, tool):
    doc = descriptions[tool].lower()
    for phrase in VOLUME_LANGUAGE:
        assert phrase not in doc, f"{tool} description still pushes edge volume: {phrase!r}"


# --- guidance shipped into other repos ---------------------------------------


@pytest.mark.parametrize(
    "template", ["rules_protocol.md.tmpl", "sink-the-ship.md.tmpl", "stop.py.tmpl"]
)
def test_templates_carry_no_volume_language(template):
    text = (TEMPLATES / template).read_text().lower()
    for phrase in VOLUME_LANGUAGE:
        assert phrase not in text, f"{template} still pushes edge volume: {phrase!r}"


def test_generated_claude_code_guidance_is_selective(tmp_path):
    """The real `gingugu init` output, not just the template on disk."""
    assert main(["--path", str(tmp_path)]) == 0

    stop = (tmp_path / ".claude" / "hooks" / "stop.py").read_text()
    command = (tmp_path / ".claude" / "commands" / "sink-the-ship.md").read_text()

    # The save nag must not tell the agent to blanket-wire its new memories.
    assert "to their cluster" not in stop
    assert "supersedes" in stop and "skip related_to" in stop

    assert "supersedes" in command
    assert "skip `related_to`" in command


@pytest.mark.parametrize("client", sorted(CLIENT_RULES_FILES))
def test_generated_rules_file_marks_related_to_as_fallback(tmp_path, client):
    assert main(["--path", str(tmp_path), "--client", client]) == 0
    text = (tmp_path / CLIENT_RULES_FILES[client]).read_text()

    assert "fallback" in text
    for phrase in VOLUME_LANGUAGE:
        assert phrase not in text.lower()
