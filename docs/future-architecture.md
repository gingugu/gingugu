# Future Architecture: From Memory Database to Cognitive Runtime

> *Status: vision document. None of this is shipped yet.*
>
> This is the long-term architectural direction for Gingugu. It captures
> what we believe the project should grow into over the next several
> phases, based on what's been built so far and what external review has
> surfaced as the real opportunity.

## The Reframe

Gingugu started as "an MCP server for AI memory." That's accurate but
small. The pieces in the current repo are pointing at something bigger:

> **A persistent cognitive runtime for agents — not a memory database.**

The current codebase supplies primitive brain functions: encode, store,
recall, associate, decay, consolidate, revise, forget, separate contexts,
track confidence. What's missing is the layer above that turns those
primitives into something that behaves more like cognition than like
file storage.

Two ideas frame the whole roadmap:

1. **Epistemic governance.** Just as ForgeSmith governs *action*
   (what an agent is allowed to do), Gingugu should govern *cognition*
   (what an agent is allowed to believe, and on what evidence). Together
   they form a closed-loop autonomous system.
2. **Memories as versioned claims backed by evidence.** Not free-text
   statements. Then an agent cannot rewrite reality invisibly — it can
   only propose new interpretations, and the system preserves who
   proposed it, why, the evidence, what it superseded, and how to roll
   back.

## Layered Architecture

We anticipate splitting the codebase into conceptual layers (one repo
initially, possibly separate packages later):

```text
┌─────────────────────────────────────┐
│  LLM / Agent                        │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│  gingugu-runtime                    │
│  • automatic recall before inference│
│  • automatic capture after          │
│  • working/procedural injection     │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│  gingugu-governance                 │
│  • classify / validate              │
│  • detect contradiction             │
│  • assess provenance                │
│  • approve / quarantine / reject    │
│  • audit trail                      │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│  gingugu-core (today's repo)        │
│  • storage / retrieval / lifecycle  │
│  • relations / tags / namespaces    │
│  • migrations / consolidation       │
└─────────────────────────────────────┘

  gingugu-mcp ──── one adapter, not the whole brain
  gingugu-ui  ──── inspection, review, correction, audit
```

## Memory Layers

A single flat memory table is wrong long-term. We expect to separate
memories into at least four logical layers (probably one table with a
`layer` discriminator, not four tables):

- **Episodic / sensory.** Raw events with provenance. User said
  something, a command produced output, a test failed, a deployment
  completed. Immutable evidence — never automatically promoted to truth.
- **Working.** Temporary state for the current task: the objective,
  open questions, hypotheses, files being modified. Aggressive expiry.
  Most of it should never become long-term memory.
- **Semantic.** Durable facts and conclusions: architecture decisions,
  user preferences, confirmed root causes, stable conventions. Requires
  evidence or governance approval.
- **Procedural.** *How* the agent operates: deployment steps, debug
  workflows, coding conventions, safety constraints. Currently lives
  in `.windsurfrules` as a giant natural-language prompt. The endgame
  is to stop pasting a startup constitution into every agent.

## The Proposal Flow

For non-trivial claims, the agent submits a *proposal* rather than
writing directly:

```json
{
  "content": "The payments service uses Redis for distributed locking.",
  "claim_type": "technical_fact",
  "source": {
    "type": "repository_observation",
    "uri": "src/payments/locks.py",
    "commit": "abc123"
  },
  "proposed_confidence": "verified",
  "scope": "project:payments",
  "reason": "Observed directly in implementation",
  "supersedes": null
}
```

The governance layer evaluates and returns:

```json
{
  "decision": "accept",
  "confidence": "verified",
  "retention": "durable",
  "review_after": "2026-09-14",
  "memory_id": "..."
}
```

…or:

```json
{
  "decision": "quarantine",
  "confidence": "inferred",
  "reason": "Contradicts verified memory M-142 and has no authoritative source"
}
```

This is the cognitive analog of ForgeSmith's authority bands: an agent
can propose and act, but governance determines what becomes
institutional truth.

Trivial saves (preferences, working memory) can still go through a
direct path. Governance is for durable claims.

## Provenance on Every Memory

Every memory should carry structured provenance, not just a free-text
metadata blob:

```json
{
  "created_by": "assistant",
  "client": "windsurf",
  "model": "claude-sonnet-4.6",
  "session_id": "...",
  "evidence": ["file:src/foo.py", "commit:abc123"],
  "user_confirmed": false
}
```

This lets retrieval distinguish between user-confirmed facts,
tool-observed facts, model inferences, model preferences, imported
records, and summarized clusters. That distinction may matter more
than vector search quality.

## Recall Returns a Memory Packet

A flat list of `(content, score)` tuples is insufficient for governed
retrieval. We expect recall to return a structured packet:

```json
{
  "claims": [
    {
      "content": "Production currently runs Kubernetes 1.31.",
      "status": "verified",
      "valid_from": "2026-04-03",
      "source": "terraform/platform/cluster.tf",
      "authority": "repository",
      "contradictions": []
    }
  ],
  "hypotheses": [
    {
      "content": "The latency issue may be caused by connection pooling.",
      "status": "inferred",
      "support": ["memory-127", "memory-149"]
    }
  ],
  "procedures": [
    {
      "name": "production-change-policy",
      "version": 4,
      "mandatory": true
    }
  ],
  "warnings": [
    {
      "type": "conflicting_memory",
      "memory_ids": ["memory-81", "memory-164"]
    }
  ]
}
```

The runtime can format this into the model's context appropriately.
Verified facts go in differently than hypotheses; mandatory procedures
get framed as constraints rather than suggestions.

## Embedded Runtime Mode

MCP is a great adapter, but it forces the agent to *remember to
remember*. The agent decides when to call `memory_search`,
`memory_store`, etc. — and different clients comply differently.

The eventual primary mode should be a runtime SDK that wraps every
model invocation:

```python
result = brain.run(
    model=model,
    user_message=message,
    identity=user,
    workspace=workspace,
    tools=tools,
)
```

Internally:

```python
context = brain.recall(user=user, workspace=workspace, task=message)

response = model.generate(
    instructions=governance.procedural_context(),
    memory=context,
    messages=conversation,
    tools=tools,
)

candidates = brain.extract_candidates(
    input=message,
    response=response,
    tool_results=tool_results,
)

decisions = governance.evaluate(candidates)

brain.commit(decisions.approved)
brain.quarantine(decisions.review_required)
```

The model still reasons. Gingugu handles continuity. From the model's
perspective, relevant memory is simply *present*.

## Multiple Cognitive Roles

A mature system likely uses different models for different roles —
not necessarily separate services, but separate logical roles:

- **Reasoning model.** Does the actual work.
- **Memory extractor.** Cheaper / more deterministic. Examines events
  and proposes memory candidates.
- **Governance evaluator.** Evaluates evidence quality, contradictions,
  sensitivity, retention, namespace.
- **Consolidator.** Periodically merges related observations into
  higher-level knowledge while keeping links to raw evidence.
- **Auditor.** Looks for unsupported beliefs, contradiction clusters,
  excessive agent-authored facts, poisoning patterns, unusual secret
  access, cross-namespace leakage.

The model that proposed a belief should not always be the sole
authority that approves it.

## Convergence with ForgeSmith

ForgeSmith governs action: *can this agent make this change?*

Gingugu governance would govern cognition: *can this observation
become durable knowledge?*

Together:

```text
Observe → Interpret → Propose belief → Govern belief
                              ↓
                          Plan action → Govern action → Execute
                              ↓
                       Observe result → Update belief
```

Two halves of the same closed-loop autonomous system. Epistemic
governance + execution governance. This is the bigger product story
and the reason both repos exist.

## What This Means for Today's Code

The current `gingugu-core` already supplies several primitives this
vision needs:

- ✅ Typed memories
- ✅ Confidence lifecycle (`verified` / `inferred` / `stale` /
      `deprecated`)
- ✅ Namespaces
- ✅ Relations (`supersedes`, `contradicts`, `caused_by`, etc.)
- ✅ Last-confirmed timestamps
- ✅ Export/import
- ✅ Consolidation
- ✅ Composite scoring (relevance / freshness / confidence / access)
- ⚠️ Metadata (currently free-string; needs typed JSON)
- ❌ Structured provenance fields
- ❌ Memory layer discriminator
- ❌ Proposal/governance pipeline
- ❌ Memory packet recall format
- ❌ Embedded runtime SDK
- ❌ True hybrid retrieval (today's pipeline gates semantic on the
      BM25 candidate pool — needs independent BM25 + vector candidate
      sets, then RRF over the union)

Everything above is roadmap, not now-work. v0.3.x stays focused on
honest framing, hardening, and the highest-value retrieval and
governance fixes that don't require a full architectural shift.

## Acknowledgments

The reframing in this document was sharpened by an external review by
Mr. Boomtastic's GPT *Tyrone* on 2026-06-14. The full transcript was
preserved in the project memory store. Several specific framings —
*"persistent cognitive runtime for agents"*, the brainstem analogy,
the proposal-flow JSON structure, and the convergence-with-action-
governance loop — come directly from that conversation.
