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

import json
import logging
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
_SEEN_LIMIT = 100_000  # every promoted memory in the target, to skip what went up

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
    re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),  # Slack
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),  # Google API key
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\."),  # JWT
    re.compile(r"[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@"),  # URL with user:password
    re.compile(r"""\b(api[_-]?key|token|secret)["']?\s*[:=]\s*["']?[A-Za-z0-9_\-.]{12,}""", re.I),
]


def contains_secret(content: str) -> bool:
    """True if the text matches any known secret shape."""
    return any(p.search(content or "") for p in _SECRET_PATTERNS)


def is_promotable(memory: dict, skip_tags: frozenset[str] = frozenset()) -> bool:
    """Apply the locked filter to one exported memory dict.

    ``skip_tags`` (lowercase) holds back more: a crew mate's own persona name,
    and whatever it asked to keep home.
    """
    if memory.get("confidence") != "verified":
        return False
    tags = {t.lower() for t in memory.get("tags", [])}
    if tags & (EPISODIC_TAGS | PERSONAL_TAGS | SECRET_TAGS | skip_tags):
        return False
    if contains_secret(memory.get("content", "")) or contains_secret(memory.get("title", "")):
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
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    from .remote_tls import verify_for

    http = httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx2.Timeout(30, read=300),
        verify=verify_for(url),  # a pinned brain is verified against its pin alone
    )
    async with http, streamable_http_client(url, http_client=http) as (read, write):
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
    return await push(
        candidates,
        [m for m in candidates if is_promotable(m)],
        source_namespace=source_namespace,
        target_url=target_url,
        target_token=target_token,
        target_namespace=target_namespace,
        contributor=contributor,
        instance=instance,
        dry_run=dry_run,
    )


async def push(
    candidates: list[dict],
    promotable: list[dict],
    *,
    source_namespace: str,
    target_url: str,
    target_token: str,
    target_namespace: str,
    contributor: str,
    instance: str,
    dry_run: bool,
) -> dict:
    """Store what the target has not seen yet, stamped; report either way."""
    async with _session(target_url, target_token) as tgt:
        seen = await _seen(tgt, target_namespace)
        fresh = [m for m in promotable if _key(m) not in seen]
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
            await _store(tgt, mem, source_namespace, target_namespace, instance, contributor, when)
            report["promoted"] += 1
    return report


def _key(memory: dict) -> tuple[str, str]:
    """What makes two memories the same promotion: title and content. Search
    results carry no metadata, so a scoped token cannot read the stamps; an
    edited source memory goes up again as its new version."""
    return (memory.get("title") or "", memory.get("content") or "")


async def _seen(tgt, target_namespace: str) -> set[tuple[str, str]]:
    """What the target already holds. A namespace not there yet holds none;
    any other refusal raises - it is never read as "nothing promoted yet"."""
    listed = await _call(tgt, "memory_namespaces", {"action": "list"})
    if not listed.get("ok"):
        raise RuntimeError(f"cannot read the target: {listed.get('error', listed)}")
    names = {ns.get("name") for ns in listed.get("namespaces", []) if isinstance(ns, dict)}
    if target_namespace not in names:
        return set()
    # memory_search, not memory_export: a crew mate's scoped token may not export.
    found = await _call(
        tgt,
        "memory_search",
        {
            "namespace": target_namespace,
            "tags": "promoted",
            "include_deprecated": True,
            "limit": _SEEN_LIMIT,
        },
    )
    if not found.get("ok"):
        raise RuntimeError(f"cannot read the target: {found.get('error', found)}")
    return {_key(m) for m in found.get("memories", []) if isinstance(m, dict)}


async def _store(tgt, mem: dict, source_ns: str, target_ns: str, instance, contributor, when):
    """Store one stamped memory in the target; a refusal raises."""
    mem["_source_namespace"] = source_ns
    tags = list(mem.get("tags", []))
    if "promoted" not in tags:
        tags.append("promoted")
    stored = await _call(
        tgt,
        "memory_store",
        {
            "content": mem.get("content", ""),
            "title": mem.get("title", ""),
            "type": mem.get("type", "fact"),
            "confidence": "verified",
            "tags": ",".join(tags),
            "namespace": target_ns,
            "source": f"promotion:{source_ns}",
            "metadata": provenance(mem, instance, contributor, when),
            "dedupe_check": False,
            "relation_check": False,
        },
    )
    if not stored.get("ok"):
        raise RuntimeError(f"the target refused {mem.get('title')!r}: {stored.get('error')}")
