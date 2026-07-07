"""``gingugu promote`` — promote local "gold" memories up to a central brain.

An MCP **client** (not part of the server) that reads memories from a source
instance, keeps only durable org-knowledge via the locked promotion filter,
stamps provenance, and stores them into a central instance — idempotently.

Stage 1: export -> filter -> stamp -> store, skipping anything already promoted.
Consolidation (multi-contributor merge) and conflict detection layer on later.

The server stays pure: this speaks the public MCP tool surface over HTTP, the
same way any other client does. Tokens come from the environment so they never
land in argv / shell history.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# --- the locked promotion filter (see the Stage 0 decision memory) ----------
# Promote only durable org-knowledge. `type` is deliberately NOT a gate — the
# repo's typing is inconsistent (the crown-jewel "verify the tag" rule is typed
# `preference`; session noise is typed `workflow`). Tags + secret-content carry
# the signal instead.

EPISODIC_TAGS = frozenset(
    {"session", "resume", "session-summary", "session-start", "session-end", "housekeeping"}
)
PERSONAL_TAGS = frozenset(
    {
        "beepboop",
        "mr-boomtastic",
        "identity",
        "collaboration",
        "communication",
        "journal",
        "reflection",
        "meta",
    }
)
SECRET_TAGS = frozenset({"key-rotation"})

# Refuse to promote anything whose content looks like a live secret — a shared
# brain must never become a credential leak (a real `sk-...` key was found
# sitting in memory content during the Stage 0 dry-run).
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"secret_access_key", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}"),
    re.compile(r"-----BEGIN"),
    re.compile(r"[a-fA-F0-9]{32,}"),  # long hashes / hex secrets
    re.compile(r"""password["']?\s*[:=]\s*\S"""),
]


def contains_secret(content: str) -> bool:
    """True if the text matches any known secret shape."""
    return any(p.search(content or "") for p in _SECRET_PATTERNS)


def is_promotable(memory: dict) -> bool:
    """Apply the locked filter to one exported memory dict."""
    if memory.get("confidence") != "verified":
        return False
    tags = {t.lower() for t in memory.get("tags", [])}
    if tags & EPISODIC_TAGS or tags & PERSONAL_TAGS or tags & SECRET_TAGS:
        return False
    if contains_secret(memory.get("content", "")):
        return False
    return True


def provenance(memory: dict, instance: str, contributor: str, when: str) -> dict:
    """Build the provenance stamp recording where a promoted memory came from."""
    return {
        "promoted_from": {
            "instance": instance,
            "namespace": memory.get("_source_namespace"),
            "id": memory.get("id"),
            "contributor": contributor,
            "promoted_at": when,
        }
    }


def already_promoted_ids(target_memories: list[dict]) -> set[str]:
    """Source ids already present in the target (read from provenance metadata)."""
    seen: set[str] = set()
    for mem in target_memories:
        raw = mem.get("metadata")
        if not raw:
            continue
        try:
            meta = json.loads(raw) if isinstance(raw, str) else raw
            src_id = meta.get("promoted_from", {}).get("id")
        except (ValueError, AttributeError):
            continue
        if src_id:
            seen.add(src_id)
    return seen


# --- MCP client plumbing ----------------------------------------------------


@asynccontextmanager
async def _session(url: str, token: str):
    """Open an authenticated MCP client session against a serve instance."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    headers = {"Authorization": f"Bearer {token}"}
    async with streamablehttp_client(url, headers=headers) as (read, write, _sid):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def _call(session, tool: str, args: dict) -> dict:
    """Call a tool and unwrap its JSON payload (robust to block shape)."""
    result = await session.call_tool(tool, args)
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    for block in result.content or []:
        text = getattr(block, "text", "") or ""
        if text.strip():
            return json.loads(text)
    raise RuntimeError(f"{tool} returned no parseable content: {result.content!r}")


async def run_promotion(
    *,
    source_url: str,
    source_token: str,
    source_namespace: str,
    target_url: str,
    target_token: str,
    target_namespace: str,
    contributor: str,
    instance: str,
    dry_run: bool,
) -> dict:
    """Read source, filter, and promote fresh gold into the target."""
    async with _session(source_url, source_token) as src:
        export = await _call(src, "memory_export", {"namespace": source_namespace})
    # memory_export wraps the payload: {"ok": True, "export": {memories, ...}}
    candidates = export.get("export", {}).get("memories", [])
    promotable = [m for m in candidates if is_promotable(m)]

    async with _session(target_url, target_token) as tgt:
        target_export = await _call(tgt, "memory_export", {"namespace": target_namespace})
        seen = already_promoted_ids(target_export.get("export", {}).get("memories", []))
        fresh = [m for m in promotable if m.get("id") not in seen]

        report = {
            "candidates": len(candidates),
            "promotable": len(promotable),
            "filtered_out": len(candidates) - len(promotable),
            "already_promoted": len(promotable) - len(fresh),
            "promoted": 0,
            "dry_run": dry_run,
        }
        if dry_run:
            report["would_promote"] = [m.get("title") for m in fresh]
            return report

        when = datetime.now(UTC).isoformat()
        for mem in fresh:
            mem["_source_namespace"] = source_namespace
            tags = list(mem.get("tags", []))
            if "promoted" not in tags:
                tags.append("promoted")
            await _call(
                tgt,
                "memory_store",
                {
                    "content": mem.get("content", ""),
                    "title": mem.get("title", ""),
                    "type": mem.get("type", "fact"),
                    "confidence": "verified",
                    "tags": ",".join(tags),
                    "namespace": target_namespace,
                    "source": f"promotion:{source_namespace}",
                    "metadata": provenance(mem, instance, contributor, when),
                    "dedupe_check": False,
                    "relation_check": False,
                },
            )
            report["promoted"] += 1
    return report


def main(argv: list[str] | None = None) -> None:
    """Console entry point for ``gingugu promote``."""
    import asyncio

    parser = argparse.ArgumentParser(prog="gingugu promote", description=__doc__)
    parser.add_argument(
        "--source-url", required=True, help="Source MCP URL (e.g. http://host:8765/mcp)"
    )
    parser.add_argument("--target-url", required=True, help="Central MCP URL")
    parser.add_argument("--source-ns", required=True, help="Source namespace to read from")
    parser.add_argument("--target-ns", required=True, help="Central namespace to write to")
    parser.add_argument("--contributor", default=os.environ.get("USER", "unknown"))
    parser.add_argument("--instance", default="local", help="Label for this source instance")
    parser.add_argument(
        "--dry-run", action="store_true", help="Report what would promote, write nothing"
    )
    args = parser.parse_args(argv)

    source_token = os.environ.get("GINGUGU_SOURCE_TOKEN", "")
    target_token = os.environ.get("GINGUGU_TARGET_TOKEN", "")

    from .config import setup_logging

    setup_logging("INFO")
    report = asyncio.run(
        run_promotion(
            source_url=args.source_url,
            source_token=source_token,
            source_namespace=args.source_ns,
            target_url=args.target_url,
            target_token=target_token,
            target_namespace=args.target_ns,
            contributor=args.contributor,
            instance=args.instance,
            dry_run=args.dry_run,
        )
    )
    logger.info("promotion report: %s", json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
