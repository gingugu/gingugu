# Gingugu Enterprise - Vision

> From personal brain to organizational nervous system.

---

## The Problem

Every AI conversation starts from zero. Organizations bleed institutional
knowledge through turnover, siloing, and forgetting. Current solutions
(RAG over docs, wiki AI assistants, "organizational knowledge" features)
are static - they search what already exists but never **learn**.

AI agents across an organization rediscover the same constraints, make
the same mistakes, and ask the same questions - every single session.

---

## The Vision

A **federated memory architecture** where every AI agent in an
organization continuously builds and shares knowledge - locally,
per-repo, and enterprise-wide.

```
┌──────────────────────────────────────────────────────┐
│               Gingugu Enterprise                      │
│    (selective absorption, org-wide patterns, RBAC)    │
└─────────┬──────────────┬──────────────┬──────────────┘
          │              │              │
      ┌───▼───┐     ┌───▼───┐     ┌───▼───┐
      │ Repo A │     │ Repo B │     │ Repo C │
      │ rules  │     │ rules  │     │ rules  │
      └───┬───┘     └───┬───┘     └───┬───┘
          │              │              │
      ┌───▼───┐     ┌───▼───┐     ┌───▼───┐
      │ Dev A  │◄───►│ Dev B  │     │ Dev C  │
      │ local  │     │ local  │     │ local  │
      └────────┘     └────────┘     └────────┘
```

### Three Tiers

**1. Local Gingugu (per developer)**
- Runs locally as an MCP server (already built - this repo)
- AI captures decisions, bugs, patterns, constraints continuously
- Private to the developer
- Lightweight, fast, zero network dependency

**2. Repo-Level Rules (the connection point)**
- Every repo has a rules file (.windsurfrules, AGENTS.md, .cursorrules)
- Rules instruct AI to connect to both local AND enterprise memory
- The repo is the glue - where local knowledge meets org knowledge
- Already exists as a pattern; just needs memory directives added

**3. Gingugu Enterprise (central server)**
- Hosted, multi-tenant, RBAC-gated
- Selectively absorbs org-relevant knowledge from local/repo interactions
- Organization-wide patterns, architecture decisions, institutional knowledge
- Admin dashboard for visibility and governance

---

## Key Capabilities

### Bidirectional Knowledge Flow
Enterprise absorbs from local/repo interactions. Local agents query
enterprise knowledge. Knowledge flows up (discoveries) and down
(standards, decisions, patterns).

### Selective Absorption
The enterprise brain doesn't vacuum every local memory. Filtering by:
- **Memory type** - architecture decisions propagate; personal preferences don't
- **Confidence level** - only verified facts rise to enterprise level
- **Tags** - org-relevant tags trigger propagation
- **Explicit promotion** - developers can flag memories for enterprise

Example: "I prefer tabs over spaces" stays local. "This AWS API has an
undocumented 5-second timeout" propagates to enterprise.

### Dev-to-Dev Memory Sharing
Peer connections between developer memory stores:
- Pair programming with shared context (no 20-minute architecture dump)
- Team-scoped memory pools
- Cross-team discovery ("has anyone dealt with this before?")

### Knowledge Retention on Departure
When an employee leaves, their local memory store preserves:
- Every decision and the reasoning behind it
- Bugs encountered and how they were fixed
- Constraints discovered (API quirks, infrastructure limits)
- The "why" behind the "what" - context that docs never capture

### Compliance and Governance
- Data classification per memory (public, internal, confidential, CUI)
- Audit trail on all memory operations
- Retention policies by namespace or classification

---

## The Foundation Already Exists

The local Gingugu - this repo - is built and battle-tested:

| Primitive | Status | Enterprise Relevance |
|-----------|--------|---------------------|
| Namespaces | Built | Maps to teams, repos, orgs |
| Confidence levels | Built | Gates what propagates to enterprise |
| Decay scoring | Built | Org knowledge ages naturally |
| Relationships | Built | Cross-team knowledge linking |
| Consolidation | Built | Dedup across teams at scale |
| Export/import | Built | Seed for sync protocol |
| Memory types | Built | Filtering for selective absorption |
| FTS5 search | Built | Ranked retrieval baseline |
| Credential vault | Built | Secret handling pattern |
| Knowledge graph UI | Built | Foundation for admin dashboard |

What makes this different: knowledge that **learns and ages** (not static
RAG), **federated** (local + enterprise, not just centralized),
**agent-native** (MCP protocol, not retrofitted human tools), and
**zero-effort capture** (AI saves continuously, not manual documentation).

---

*Interested in the enterprise direction? Open an issue or reach out.*
