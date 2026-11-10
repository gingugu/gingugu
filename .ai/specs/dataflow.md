# Data Flow

## Store

```
memory_store(content, title, type, namespace, tags, confidence, provenance, about)
  → handlers/memory.py validates + defaults (confidence="inferred" if unset;
     provenance parsed against its controlled vocabulary if given)
  → storage.py inserts into `memories`, including `provenance` and `about`
  → FTS5 trigger mirrors the row (title, content, about) into the full-text index
  → embedding_text.embedding_input(title, content, about) builds the text;
    embeddings.py computes the semantic vector from it, and chunking.py pieces
    it into `memory_chunks` when the backend exposes token offsets (head-only
    otherwise). `about` sits between title and content, inside the encoder's
    window; a memory with no `about` embeds the same text it always did
  → claim_sync.sync() derives state claims from title+content into `memory_claims`
     (claim_qualify resolves each ref's repo: URL > repo named beside the ref >
      a binding stated elsewhere in this same memory > the namespace default)
  → dedupe/relation check → returns { ok, memory, similar_memories[],
                                      suggested_relations[], contradicted_memories[]? }
```

`provenance` declares how the writer came to believe the memory
(`user-asserted`/`measured`/`file-derived`/`self-concluded`); `about` declares
what it is for, in the user's own words. Both are optional, both default to
unset, and neither advances `last_confirmed` - declaring them is not
confirming the claim. `memory_update` accepts both too, with the `metadata`
convention (`None` leaves the field alone, `""` clears it); changing `about`
re-runs the embed step above and re-syncs `memories_fts`.

`similar_memories` = merge candidates. `suggested_relations` (excludes self +
already-linked + items already in `similar_memories`) = memories to **examine
for a directional relationship**. Both are hints; neither blocks the write.

Both are **two-stage**, and the stages answer different questions:

1. **Find** with hybrid retrieval (`search.py`). RRF fusion is good at "what is
   nearest".
2. **Adjudicate** with an absolute measure (`similarity.py`) and gate on that:
   cosine over the stored embeddings, or token Jaccard when embeddings are
   unavailable. Each hit reports `similarity` + the `basis` it was measured on,
   and no retrieval `score`.

Stage 2 is not optional garnish. The fused RRF relevance is a function of a
candidate's **rank** in the pools, normalized so rank 1 in both maps to 1.0,
and something is always rank 1, so the top hit trended toward 1.0 for every
payload ever written, while both gates (0.5 and 0.3) sat below what the
arithmetic could even produce. Measured on a 1,423-memory brain: the payload
"Lunch was a tuna sandwich" scored **0.9262** against a corpus of engineering
notes, the identical score two other unrelated payloads got in a different
namespace. The practical cost was three merge candidates and three relation
candidates on **every** store, each needing a manual read to dismiss.

Cutoffs are calibrated, not guessed: 228 `supersedes` pairs as positives
against 7,688 random same-namespace pairs. Cosine `0.80` and Jaccard `0.15` sit
at the same operating point (~8.5% of random pairs admitted, ~85% of genuine
near-duplicates kept), so turning embeddings off changes the instrument's
precision but not the meaning of the gate. Relations use a softer `0.72`/`0.10`:
they are candidates to examine, not merge proposals. Do not port these numbers
to another corpus unexamined; BGE cosine does not bottom out near zero, and two
unrelated memories from one brain sit around 0.71 simply from shared register.

An empty hint list is the **common** case and the signal working.

`suggested_relations` is deliberately _not_ a link list. Overlap is how a
candidate is found; what justifies an edge is a fact similarity cannot see
(`supersedes`, `contradicts`, `caused_by`, `parent_of`/`child_of`). Recall
already ranks by hybrid text + semantic score, so a `related_to` edge meaning
"same topic" duplicates the index and buys nothing: spreading activation caps
at 3 neighbours per seed and now ranks them by `relation_type`, so such an edge
forfeits its slot to a directional one. Measured 2026-08-04, before the
guidance was reversed and while the traversal was still type-blind: 69% of a
real brain's 1369 edges were `related_to`, and they were actively crowding out
the edges that carried signal.

Both are **always compact** (title + ~200-char `summary`), unlike the `memory`
the caller just wrote, which returns in full. A hint is a pointer: enough to
decide merge/link/ignore, with `memory_recall` one call away. Full bodies here
charged up to six memories of context to every write — measured on an
821-memory corpus, ~11,300 chars per store, frequently larger than the memory
being saved.

`contradicted_memories` is the write-time reconciliation hook: when this write
records a ref as resolved, every memory in the same namespace still asserting it
open is returned. **Omitted rather than empty** when there is nothing to report —
an always-present empty list is noise in every response, and this hint has to
stay cheap to ignore. Claim extraction is best-effort: a failure logs and is
swallowed so it can never break a write.

Claims are derived from text, so `memory_update` re-syncs them whenever title or
content changes — but NOT on a confidence/tag/type-only update, matching the
embedding re-encode condition.

That same "did the text move?" test also advances `last_confirmed`: **a rewrite
is a confirmation.** Someone re-read the claim and restated it. Retypes, tag
edits, metadata writes and no-op updates assert nothing about truth and leave
the clock alone. Before this, `last_confirmed` only ever moved when a caller
explicitly passed `confidence="verified"`, so ordinary content maintenance never
registered and the freshness signal — which drives scoring, the spread-neighbour
sort, staleness and `age` — silently rotted. Accepted trade: a one-word typo fix
also resets the staleness clock.

Under `gingugu serve` a scoped token's grant (`grants.py`) is bound for the
call and consulted at the same store functions every step above runs through:
the filter builder that feeds both candidate pools, `fetch_by_ids`, the
neighbourhood walk, and the access-credit write. A namespace-less recall under
a grant means the grant's namespaces, widening stays inside it, and a row
outside it is simply absent - the same as not existing. A stdio server started
with `MEMORY_GRANT` (a warm minion's own server) binds its grant the same way.
stdio without it, CLI, background passes and the owner token carry no grant and
flow exactly as described.

A remote client authenticates differently. The stdio proxy (`proxy.py`) trades
the machine's owner token at `POST /token/derive` for a short-lived derived
token (in memory, SHA-256 only, TTL <= 1h) and sends only that on `/mcp`;
`BearerAuthMiddleware` resolves it through `DerivedTokens` and tags the request
`token_kind=derived`. The derived grant carries the client's `MEMORY_GRANT` and
a `home` namespace (its `MEMORY_NAMESPACE`). A call that names no namespace
defaults to that home rather than the server's configured namespace (read by
`NamespaceManager.resolve_name` and `handlers/scope.read_scope`), and a derived
grant never reaches `credential_*`. A destructive call over the proxy (a hard
forget, say) takes `confirm` exactly as locally. With `confirm` omitted the brain's
elicitation relays through the proxy unchanged and the user's answer relays back -
except for a client known to show no dialog (Claude Code's VS Code extension), whose
`initialize` the proxy forwards without `elicitation` (`confirm.withhold_dialogs`),
so the brain tells it to ask the user and call again with `confirm=true`. A big
delete with `confirm=true` is screened by the proxy first (`proxy_gate.screen`, from
`Link.forward`): the person's approval is on this machine, so the proxy takes it
(`destructive.take`) before relaying, and refuses without it. The
proxy's own `credential_delete` goes to the local vault's in-memory MCP
session (`proxy_vault_link.VaultLink`), which checks the gate itself; its requests go to the client as
`vault-<nonce>:<id>` (a per-process nonce) so they never collide with the brain's.

## Reconcile

```
memory_stats(review_limit=100)     → claims { open, open_actionable, resolved,
                                              unverified, contradicted, sample[] }
memory_search(claims="open")       → full bodies of the backlog (or ids="…")
memory_search(claims="unverified") → refs named but never resolved in prose
memory_update(resolve_claims="…")  → marks claims resolved; PROSE UNTOUCHED
```

`resolve_claims` takes comma-separated refs or `"all"`. It writes
`resolved_state`/`resolved_by`/`resolved_at` on the claim row and leaves the
memory body byte-identical, because a dated record that said "PR #10 open" was
accurate when written. Use `content` only when a memory asserts something that
was never true. No new tool was added for this loop — `memory_search` gained a
`claims` filter, and the v0.8.0 fetch-by-ids sweep still works.

**`sample` enumerates, it does not illustrate.** It first listed only the
_contradicted_ subset — defensible (those are answerable from what the brain
already holds) and unusable in practice: a namespace could report five open
claims and give the caller no way to learn which five, so a real sweep dropped
to raw SQL against the live database. It now lists every open claim,
contradicted ones first, each row tagged `contradicted`.

Two counts because they differ: `open` is every unresolved claim, while
`open_actionable` — what `sample` lists — excludes claims on deprecated
memories. Reporting only `open` invites a caller to compare it against
`len(sample)` and conclude rows went missing.

**`unverified` is a third state, not a third slice of the backlog.** A ref the
prose names without ever saying what became of it used to produce no claim at
all, so `claims.open` read 0 while the memory read as in-flight forever. The
obvious repair — call it open — was measured and rejected: 185 such claims exist
against 223 real ones, and nearly all narrate work that already shipped, so
adopting them would have buried the backlog in history. It is therefore excluded
from `open`, `open_actionable`, `sample`, and contradiction detection (which
requires two assertions, and silence is not one), and read on its own through
`memory_search(claims="unverified")`. `resolve_claims="all"` stays open-only for
the same reason; naming the ref explicitly still resolves it.

`claim_queries.claim_filter()` is the single definition of both predicates,
shared by the stats block and `memory_search(claims=…)` through
`search_common.build_filters`. It contributes no bound parameters, so it
composes with the FTS5 join, the embeddings join, and a plain table scan
alike. Contradiction stays scoped **within** a namespace — bare refs are keyed
off the namespace's default repo, so cross-namespace matching would pair two
different repos' `PR #12`.

## Recall

```
memory_recall(query, namespace | "ns1,ns2,…", filters)
  → search.py: independent BM25 (FTS5) + semantic (cosine) candidate pools,
    RRF-fused over their union; semantic-only matches join above a 0.55
    similarity floor (≤ 5 entrants), cohort members never displaced
  → the semantic cohort is a FIXED 40, never scaled by limit: a rank only
    means something against a fixed cohort, so sizing it by the caller's row
    count made relevance move with `limit`. `search(q, k)` is now exactly the
    first k of `search(q, K)`. Ties break on id, not on set iteration order
  → a cohort member past the encoder's window is scored on ONE piece from
    `memory_chunks`, chosen lexically - the piece holding the most distinct
    query words, ties to the head; cosine never chooses. Entrants (outside the
    keyword pool) keep their head vector; only a backend exposing token offsets
    pieces memories at all, so Ollama/disabled embeddings stay head-only
    (see `chunking.py`)
  → multi-namespace: one ranked SQL pass over all listed namespaces
    (IN clause); limit caps the TOTAL list (unlike context's per-namespace limit)
  → namespace omitted: under a derived token with a home, that home first;
    else the configured namespace, or EVERY namespace when the
    server has none configured (scope: "all") - never the `default` fallback,
    which is for writes (handlers/scope.py)
  → scoped and empty: rerun once with no namespace filter, every other filter
    kept; response carries widened_from + scope: "all", even if still empty
  → blend with recency + confidence + access frequency
  → if include_related: hub-dampened neighbourhood appended (via_relation=true) —
    ≤3 neighbours per seed (confidence, then low degree, then recency), ≤10 total
  → ranked list; every memory stamped with its home namespace
    (compact=true: title + ~200-char summary instead of full content,
    related extras compacted too - keeps broad recalls under MCP clients'
    tool-result token caps; access is still credited). `provenance` rides
    along in both shapes when set, so a self-concluded claim stays visibly
    contestable; `about` is in the full shape only. Both are omitted when
    unset
  → every memory carries a derived `age` ("2 days ago"), computed at
    serialization and never stored - kept in compact mode even though raw
    timestamps are dropped. Anchored on the freshness anchor, so a memory
    maintained since it was written reads "7 weeks ago (updated just now)"
    instead of looking 7 weeks stale
  → query_log row (query_log.record, via the store): the query text, the
    ids returned in rank order (de-duplicated), the scope actually searched
    (`*` when unscoped or widened), the MCP session id. A zero-hit recall is
    still a row. Best-effort: a failure logs a warning and the recall returns
    normally
  → capability lane (skipped when the caller passed `type`): a second,
    capability-only pass - cosine of the raw query against every
    non-deprecated, non-superseded `capability` memory in the same scope the
    main search ran in, kept at ≥ 0.68 (`capability.LANE_BAR`), best 2, never
    an id already in the response. Returned as `capabilities` (compact shape +
    `similarity` + `capability` + `namespace`), absent when empty. Not credited
    as an access, not a spreading-activation seed; its ids (and the related
    extras') go to the query_log row's `extra_ids`, written after the lane so
    an act on either finds this query (migration 022).
    No embedder, no lane: there is no calibrated lexical floor to fall back to
```

A `capability` memory records that a thing exists and how to run it, as distinct
from a `workflow` that says how to do something by hand. Its structure lives in
`metadata.capability` = `{run, path?}`, validated on store and on the final state
of an update: the type and the block come together or not at all. Every read
shape (full and compact, every surface) carries `capability: {run, path?,
exists?}`. `exists` is a plain filesystem check made at read time - a relative
`path` resolves against the namespace's repo `path`, and with none on record it
is `null`, as it always is under `gingugu serve`, where the server's disk is not
the caller's. The separate lane exists because a pointer competes in the main
ranking against the very procedure memories it replaces, which are many, long
and frequently read. Its floor was measured on recall queries, not borrowed from
the prompt gate: short queries score lower against the same memory than whole
prompts do, and the gate's 0.78 missed most relevant queries.

A cohort member longer than the encoder's fixed token window carries pieces in
`memory_chunks` (migration 013): window-sized slices cut on the encoder's own
tokens, each with its own vector. At query time exactly one piece stands in for
the memory - the one holding the most distinct query words, ties to the head -
never the piece cosine ranks best. Scoring by best match was measured first and
rejected: every long sibling then gets several chances to match, and the tail's
gain comes straight out of the head's score. Memories outside the keyword pool
are unaffected, since measured there pieces added nothing; only
`FastEmbedProvider` exposes the token offsets pieces need, so Ollama and
disabled embeddings stay head-only. Real brain copy, 383 position-stratified
questions: hybrid mrr 0.2675 → 0.284, tail mrr 0.197 → 0.256, head 0.337 →
0.323, +7ms/search.

`memory_search`'s `sort_by` selects the retrieval path rather than reordering
one:

```
sort_by = relevance | decay_score
  → with a query:    search.py, the hybrid engine above
  → without a query: score every matching row (six columns, no bodies), take
                     the top `limit`, then fetch those bodies by id
sort_by = created | accessed
  → with a query:    the FTS match set, ORDER BY the column, LIMIT in SQL -
                     no BM25 ranking and no semantic cohort, because a date
                     asks what relevance cannot answer. No `score` returned
  → without a query: ORDER BY the column, LIMIT in SQL
ids = "a,b,c"
  → exact fetch, requested order, deprecated included, `missing` reported
```

A `memory_search` that carries a `query` also writes a `query_log` row
(`tool='search'`, same shape as recall's). An `ids` fetch or a filter/sort
listing with no query is not a question and is not logged.

Every path selects its rows in the order it returns them. Sorting a pool that
was truncated on a different axis reorders a biased sample rather than the
corpus, so a row that lost the earlier cut is unreachable however well it
matches the sort - which is why `sort_by="created"` used to return neither the
newest rows nor a stable answer as `limit` changed. Ties break on `id`.

`memory_search` takes the same namespace forms (single, CSV, or omitted =
all namespaces). Unknown namespaces error and name the missing one(s) — reads
never mint namespaces. It widens on empty like recall, but only with a `query`:
an empty filter-only sweep (`claims`, `orphans`, `pinned`) is a real answer, and
padding it with other namespaces' rows would misdirect a reconciliation sweep. Single-namespace-only tools return a comma-hint when
handed a CSV value, and `memory_store` rejects CSV outright rather than
minting a junk namespace named `"a,b"`.

`explain=True` (recall, search, context) adds the terms behind each `score`:

```
score_breakdown = {relevance, freshness, access, confidence}  weighted, sums to score
                + type_boost                                  context only, where applied
  → decay.score_parts() computes them; score_memory() is the SUM of that call,
    so the explanation and the ranking are one arithmetic, not two
  → no breakdown where there is no composite to decompose: pinned memories
    (never ranked), an `ids` fetch, a created/accessed sort, a bare relevance
  → a constant relevance term across several context hits is the signal that
    those came from the recency or cross-namespace bucket, which score on a
    synthetic relevance rather than on any match
```

## Excerpt (reading inside one memory)

```
memory_excerpt(memory_id, query? , start?, end?, max_matches, context_chars)
  → excerpt.py: literal, case-insensitive-by-default substring scan.
    No ranking, no stemming, no model - the same answer every time
  → query:       each match returns {start, end, line, excerpt}; offsets are
                 absolute in the full body, so a hit feeds straight back as a range
  → start/end:   exact character slice; omitted bounds mean body start/end,
                 out-of-range clamps, inverted bounds swap
  → both:        search runs INSIDE the range, offsets still absolute
  → neither:     `length` + `lines` only, which is how you size a memory first
  → total_matches is the TRUE count even when max_matches caps the list, so a
    caller can tell "all of them" from "the first 10 of 300"
  → credits a real access, like memory_search(ids=…). No spreading activation:
    it traverses no relations
```

## Context (session priming)

```
memory_context(namespace | "ns1,ns2,…", task_hint, limit, compact)
  → pinned memories load FIRST, unranked and additive to limit (cap 20/ns)
  → context.py selects top-N per namespace by relevance-to-hint + value signals
  → selection order ≠ presentation order (see below)
  → multi-namespace calls de-dupe across loads (highest-scoring instance wins);
    each namespace's pins lead, then the ranked tails interleave by rank;
    each memory is stamped with its home namespace
  → the pin loop emits its OWN instance; "highest-scoring wins" governs only
    the ranked tails, so a pin never arrives carrying another bucket's score
  → spreading activation wakes related dormant memories
  → returns the working set the agent should hold for the session
    (compact=true: title + ~200-char summary instead of full content,
    plus the derived `age` - the protocol mandates compact here, so dropping
    every temporal signal left the agent time-blind while reading the
    RESUME memory)
  → task_hint given: one query_log row (tool='context'), ids in rank order.
    No task_hint, no row
  → a persona caller (not a no-persona stdio "local", "unknown", or a warm
    "minion") also gets board: {"unread": N}, its board inbox size
```

Context loads are protocol-driven reads: they refresh `last_accessed` (dormancy
clock, via `touch_many`) but do **not** bump `access_count` or write
`access_log` rows - those are reserved for `memory_recall`/`memory_search`
hits, so session-start loads can't inflate the access ranking signal. The
`task_hint` is the exception that proves the split: it is a question, so it is
recorded in `query_log` - a different table, which does not feed ranking.

That reservation is what makes `access_log` a clean record of _deliberate_
retrieval, and since 2026-08-30 each row also carries the id of the MCP session
that asked (`access_log.context`, from `session.current_session_id`). Rows from
one session share a key, so the log answers "which memories are retrieved
together" and not only "when was this read" - the co-access signal, derived
from behaviour rather than authored. The id is per process on stdio and a
digest of `Mcp-Session-Id` over HTTP (`inbound.py`). It is `NULL` when no
session is in flight, or for a sessionless (2026-07-28) HTTP client; a
placeholder would fake co-access between unrelated rows.

**The signal is a rolling window, not a permanent record.** `prune_access_log`
deletes rows past `ACCESS_LOG_RETENTION_DAYS` (90), so anything built on
co-access must accumulate its own durable aggregate rather than expecting to
re-derive the full history from the log later. `query_log` is the opposite: it
is **never pruned**, because nothing summarises it and its rows are the only
copy of what was asked. `query_acts` (migration 022) is never pruned either:

```
memory_search ids | memory_excerpt | memory_update | memory_relate | memory_forget
  → on success: store.log_act(act, ids) → query_acts.record
    → the same MCP session's last 200 query_log rows, newest first; the first
      that returned the memory claims it: in its result_ids → query_id + rank
      (1-based); in its extra_ids → query_id, rank NULL; none → both NULL
    → no session: no lookup, the row is still written (session_id NULL)
    → one row per distinct memory (a relation logs both ends); best-effort,
      never commits, swallows sqlite3.Error
```

The Explorer's `/stats` is the one reader that must
not prune: it passes `prune=False` to `compute_stats`, so looking at the brain
never deletes `access_log` rows.

That refresh is also why the recency bucket must not be ordered by
`last_accessed`. It was, until 2026-08-26, and the result was a feedback loop:
each load refreshed the clock on everything it surfaced, lifting exactly those
rows to the top of the next load's bucket, so the bucket converged on what it
had already shown and a newly-stored memory could not break in. The bucket
orders by `updated_at` instead - a write timestamp no read path touches - which
is what the bucket was always documented to mean. Nor does bookkeeping move it:
`storage.update` advances `updated_at` only when one of the record's own fields
changes value, so resolving a claim, replacing tags or pinning leaves a note
where it was in the bucket. Dormancy and bucket ordering
ask opposite questions ("has anyone touched this lately" vs "is this new to
me"), so they get two columns rather than one column serving both.

A consequence worth knowing: because the protocol calls `memory_context` every
session and that refreshes the dormancy clock, anything it routinely surfaces
can never accumulate `DORMANT_AFTER_DAYS` untouched. Dormancy only ever reaches
the tail. Intended, and now pinned by `tests/test_dormancy_lifecycle.py`.

**Selection order is not presentation order**, and conflating them is what put
the pinned tier at the bottom of every payload. Selection fills the recency
quota _first_, because filling it first is what stops a contended `limit` from
evicting the "where we left off" memory - that is a survival question.
Presentation then answers a different one, "what should the agent read first?",
and emits by bucket membership: task hits (the caller asked a question), then
recency, then cross-namespace, then the score-ordered backfill. Membership, not
which quota happened to claim the row - recency is filled first, so a
task-relevant memory that is also recent would otherwise be presented as though
it had never matched the query.

Neither layer re-sorts by composite score, because that score is not comparable
across buckets: only the task bucket has a real search relevance, while the
recency and cross-namespace buckets carry a fixed `relevance=0.5` placeholder
(they have no query to be relevant _to_). Sorting on it ranks rows against each
other on a scale none of them share - and silently undoes the quota that just
protected the recency slot. Measured on a copy of the live brain, the old global
sort placed **0 of 8** pins in the top 8; the fix places **8 of 8**.

**Pinned memories** sit outside all of this. Ranking answers "what is most
relevant to this task?"; it cannot answer "what must never be missing?". A pin
(`memory_update(pinned=True)`) removes a memory from the ranking contest
entirely - it is not scored, not quota'd, and not evictable - and is returned
_in addition to_ `limit`, because a tier that truncates under contention
recreates the failure it exists to fix. Bounded by `PINNED_HARD_CAP = 20` per
namespace at the write path instead. Deprecation beats a pin.

**Scorelessness is the pin's identity, not a cosmetic detail.** The absence of
a score is how a caller knows a memory bypassed ranking, so a pin that arrives
scored is indistinguishable from an ordinary ranked hit and its place at the
top reads as earned rather than guaranteed. Until 2026-08-31 the multi-namespace
merge broke exactly that: de-duplication keeps the highest-scoring instance,
`(mem.score or 0.0)` reads a pin's `None` as 0.0, and any scored duplicate won.
A memory pinned in one namespace that also reached a second namespace's
cross-namespace bucket therefore came back wearing that bucket's score.
Position was never wrong; identity was. Single-namespace loads never showed it,
because the pin surfaces once and there is no duplicate to lose to. A memory
pinned in a namespace the caller did not ask for still arrives as a ranked hit,
which is correct: it is not part of the requested namespace's unconditional tier.

**Auditing the tier** is a read path of its own, since the tier is the one set
paid for on every call:

```
memory_search(pinned=True)   → every pin, any namespace; tri-state, so
                               False returns the complement and omitting it
                               ignores the flag. Composes with every other
                               filter, with or without a query
memory_stats → stats.size    → total_chars, mean_chars, pinned_chars,
                               largest_pinned_chars. pinned_chars is the only
                               recurring cost in the store; largest_pinned_chars
                               is the skew check, since a tier dominated by one
                               entry IS that entry and is fixed by splitting it,
                               never by adding better pins alongside it
```

## Involuntary recall (the prompt is the query)

```
UserPromptSubmit event  →  .claude/hooks/user_prompt_recall.py (pure stdlib)
  → length floor HERE, before the package is imported: ~44% of real prompts
    are acknowledgements, and rejecting them costs bare interpreter startup
    instead of ~410ms of package + ~500ms of encoder. Exception: with
    MEMORY_PERSONA set (non-blank) a short prompt goes through too, for its
    board mail (the package load, never the encoder)
  → survivors piped to `gingugu hook prompt`
      → BOARD MAIL first, on EVERY prompt of a persona agent, before the
        length gate (board_hook.mail): the persona's unread inbox, fenced by
        the client's MEMORY_GRANT (hook_grant.client_grant), not the recall
        namespaces; minus ids already shown this session (key board:<session>
        in hook-sessions); read-only, nothing marked read. At most 5 posts, a
        300-char JSON-encoded preview each, each with its board(action="read")
        call; a task also with its claim call when open, or its holder
        (persona, machine, session) when claimed. Never raises. A post body with a line of its own `xray: <range>`
        also gets its X-ray brief appended (X20: the first such post, once per
        prompt, <= 3500 chars, rendered from the prompt's cwd, or from the local
        checkout of the repo an `xray: <repo> <range>` line names; xray/board_brief;
        a cut brief ends with board_brief.CLIPPED)
      → then _recall (below); a recall exception does not silence the mail
      → strip_affect(): greetings/interjections/emoji removed, because they
        carry REGISTER and the corpus is full of reflections written in the
        user's own voice. Matching voice is the dominant false positive.
      → encode → cosine sweep over crow + <MEMORY_PERSONA, if set> + <project_name(cwd): the folder, or a worktree's main checkout>
          (namespaces_for: crow, then persona, then the repo, deduped; a
          malformed MEMORY_PERSONA is ignored; any name the CLIENT's grant
          cannot read is dropped - hook_grant.client_grant: what the client's
          gingugu servers published (client_grants; a client under 3s old is
          waited on), which the hook's own MEMORY_GRANT must agree with - and
          a grant stdio_grant rejects, a disagreement, an unreadable
          publication, or a fenced client with no live server yields [] -
          the hook exits quiet before any lookup)
          EXCLUDED IN SQL: deprecated; pinned (already loaded unconditionally
          at session start, so injecting one pays twice); superseded (the
          store has already recorded it as replaced)
      → BM25 over the same prompt: a hit must ALSO match lexically
      → gate: bar 0.78 AND (score - median of the full sweep) >= 0.15
      → cap 3, minus anything already injected this session
  (first of all, recall_gate.strip_notifications drops Claude Code's
   `<task-notification>` blocks - client and brain - so a notice alone recalls
   and logs nothing, and real text around one is what gets searched)
  → log_prompt: the affect-stripped prompt + the ids actually injected go to
    query_log (tool='hook'), BEFORE the early return, so a prompt that
    surfaced nothing is still a row. The sweep stays read-only; this opens its
    own short write connection (mode=rw, never creates a file, 0.5s busy
    timeout), swallows every exception, and skips a store not yet at
    migration 015. Rows carry Claude Code's session id, so they do not join
    to access_log
  → hookSpecificOutput.additionalContext: mail and memories in ONE block,
    systemMessage "gingugu: N memories surfaced, M board posts" (each part
    only when nonzero); or silence
```

**The margin is the mechanism, not the bar.** An absolute threshold admitted
so much that the cap was doing the selecting: 207 of 224 firings hit it. A
threshold that only ever truncates a larger set is not a filter. Scoring each
hit against the median of its OWN sweep makes the test relative, so a prompt
with nothing distinctive to say fires nothing however high its absolute scores
drift. The median must come from the full sweep - taken over an already
filtered head it would describe the winners rather than the field they beat.

The asymmetry driving every choice here: a miss costs nothing, because the turn
proceeds exactly as it would have. A false positive costs real damage, because
injected context arrives with the authority of the system rather than the
tentativeness of a search result, and nothing downstream marks it as a guess.
So each stage REJECTS, and none of it is a judgement call - the same rule that
governs truth status governs this.

`bench/gate.py` reproduces the sweep against a real corpus. Read `cap3`.

## Tripwires (PreToolUse)

```
PreToolUse event  →  .claude/hooks/pre_tool_tripwire.py (pure stdlib)
  → on the main thread, drops mcp__gingugu__* HERE, before the package is
    imported: our own tools are how a bad tripwire gets repaired, so they never
    trip (inside a subagent they pass through - see "Warm minions" below).
    Exceptions: a board call with action "post" passes, for the X20 nudge;
    tool_hook still skips memory tripwires for it (_SELF_PREFIX); and the five
    destructive tools (memory_forget, memory_namespaces, memory_consolidate,
    memory_import, credential_delete) pass, for the big-delete gate
  → survivors piped to `gingugu hook tool`
      → minion fence / spawn stash first (see "Warm minions"; independent of
        MEMORY_TRIPWIRES)
      → checkout guard (checkout_guard; MEMORY_CHECKOUT_GUARD, own switch)
          Bash only; heredoc bodies dropped, quote-aware split into segments;
          per segment, a git switch / checkout (unless paths follow --) /
          rebase (not --abort|--quit) / reset --hard|--merge|--keep /
          stash (not list|show), also behind env / command / VAR=, in the
          dir that segment runs in (cwd, a prior cd, git -C; ~ and $VAR
          expanded), one rev-parse per distinct dir
          that dir is a PRIMARY checkout (git-dir == git-common-dir)
            → deny EVERY time, pointing at a worktree; the hook exits here
          GINGUGU_PRIMARY_OK=1 prefix, a linked worktree, or any failure: pass
      → big-delete gate (tool_hook._destructive → destructive.hook_output):
          a big delete → permissionDecision "ask" (Claude Code's prompt) and a
          single-use approval written; the hook exits here. Chain: minion fence →
          checkout guard → big-delete gate → X-ray
      → X-ray edit check (xray/edit_hook; its own switch, runs before the
        MEMORY_TRIPWIRES check and does not exit the hook)
          Edit/Write/MultiEdit of a file an X-ray adapter parses (lang.for_path:
          Python, TS/JS, Go), else skip
          MEMORY_XRAY_TRIPWIRE=off: skip
          git root + content fingerprint of the checkout
          graph cache hook-sessions/xray-graph-<sha16(root)>.json (shared with
          the map hook below; lives in xray/hook_common); rebuilt
          locally with the code_xray extractor on a fingerprint miss,
          re-parsing only files whose bytes changed (parse cache
          hook-sessions/xray-parse-<sha16(root)>.json, X16; linking reruns)
          blast radius of the file (rank.blast, walking through package nodes
          but never listing one); nothing imports it → skip
          read claim hook-sessions/xray-read-<session>/ (X14 neighbourhood shown,
          or the file written by this session) → skip: not a blind edit
          already claimed in hook-sessions/xray-shown-<session>/ → skip
          claim it atomically (O_EXCL; lost race or failed write → skip), then the
          X-ray reason: direct importer count, reach and candidate-test totals,
          top 5 direct importers by rank, top 5 nearest tests (a test named for the
          file first); for an Edit/MultiEdit, `Edit lands in` lines for the
          definitions each old_string sits in (X17, xray/edit_symbols) and a
          lower-bound line; the same scar tissue lines as on a Read (X3: the
          blind edit is the one that never saw them); import-based-estimate
          caveat last
      → X-ray Bash snapshot (xray/tool_xray.reason → xray/bash_hook.before; same
        switch MEMORY_XRAY_TRIPWIRE; never denies, prints nothing)
          see "X-ray of a shell command's writes (X15)" below
      → MEMORY_TRIPWIRES=off: skip memory tripwires (not the X-ray above)
      → namespaces_for(cwd) (narrowed by the client's grant; [] → exit before any lookup)
      → load_tripwires(crow + <persona> + <project_name(cwd): the folder, or a worktree's main checkout>)   read-only SQLite
          skips deprecated and superseded memories; pinned ones DO trip
          (a pin loaded at session start is not in front of the agent now)
      → matching: tool_pattern FULL-matched against the tool name,
        input_pattern SEARCHED in match_text (Bash -> command; Edit/Write/Read
        -> file_path; NotebookEdit -> notebook_path; else sorted-JSON input)
        one hit per memory, at most 3
      → drop memories already tripped this session
        (hook-sessions/tripwires-<session_id>.json, its own file beside the
        prompt hook's state)
      → combined deny: if the X-ray also fired, its reason follows the
        tripwire reason after a blank line, in ONE deny
      → first match: permissionDecision "deny", reason = title + summary +
        "re-issue unchanged to pass"; systemMessage tells the user a tripwire
        stopped a call; ids recorded in the state file; one query_log row
        (tool='tripwire', query = the matched text, result_ids = memory ids)
  → the re-issued call finds its memory already tripped and passes
```

**Deny, not inject.** A `PreToolUse` `additionalContext` is delivered next to
the tool *result*, after the call ran, so it cannot warn in time. Denying is the
only way to speak first; the retry passing keeps the block to one interruption
per memory per session. Misses are not logged (the hook runs on every tool
call), and every failure exits 0 silently, which is the normal permission flow.
X-ray denies are not logged to `query_log` (memory trips still are).
In remote mode the brain runs `load_tripwires` and the client matches the
rules it returns (see **Hooks in remote mode**).

Matching is pure regex: no encoder, no model judgment, since a risky command
and a harmless one can read alike to an embedding. `memory_tripwire` manages
the rows (`add` / `list` / `remove`) and `test` is a dry run of the same
matching that never denies and never logs. Accepted v1 limitation: after
context compaction the memory may drop out of context while suppression still
counts it as shown.

## X-ray map on arrival (PostToolUse)

```
PostToolUse event (Read | Grep | Glob)  →  .claude/hooks/post_tool_xray.py (pure stdlib, 15s)
  → `gingugu hook read` (post_tool_hook) → xray/read_hook
      MEMORY_XRAY_MAP=off, no session id, or no git root: skip
      graph: xray/hook_common cache hook-sessions/xray-graph-<sha16(root)>.json
        (shared with the edit hook); on a fingerprint miss, rebuilt locally
        with the code_xray extractor, re-parsing only files whose bytes changed
        (xray-parse cache, X16). A build that never finished leaves a
        marker in hook-sessions/xray-build-<session>/ and is not retried this session.
        Nothing parseable in the repo → skip
      first Read/Grep/Glob of the repo this session
        (claim hook-sessions/xray-map-<session>/, O_EXCL) →
        repo map: file count, languages, import edges, tests, top 8 files by
        rank with importer counts, entry points
      Read of a source file an adapter parses, once per file per session
        (claim hook-sessions/xray-read-<session>/) →
        up to 4 lines: rank k of N, direct importers, nearest tests, direct
        imports; any file X-ray parses adds `Most called: ...`
        (X17 symbol cache, any language; also shown for a file with no import
        neighbourhood, e.g. a lone Go file); otherwise silent for a file with no edges
      scar tissue (X3, MEMORY_XRAY_SCARS; xray/scars.py): the same first Read also
        looks up the memories that name the file: the repo's own project namespace
        only (the main checkout's folder name in a worktree; never crow), FTS5
        phrase match on the basename (xray/scar_query, local DB or POST /hook/scars),
        hits resolved against the checkout's source files and ranked by type weight
        (doubled when the title names the file) over files named; appends `Scar tissue: N memories name this file:` and the
        top 3, and a leaf file with no neighbourhood gets them under a
        `GINGUGU X-RAY: <path>.` header. Derived at read time, nothing stored
      hidden partners (X4, MEMORY_XRAY_HISTORY; xray/history + hotspots.partners),
        before the scars: history.read (HEAD → cached xray-history-<sha16(root)>.json,
        else git log --no-merges -M --name-status -z -n 1000 → parse, renames followed →
        churn, author counts, pairs shared >= 5) → code-maat coupling for this file
        (>= 5 revs each, degree >= 30%) with no import either way → `Changes with (no
        import): <file> NN%`, top 3; any failure drops only this line
  → emit additionalContext beside the tool result (output capped under 9000 chars)
```

**Inject, never block.** The map is delivered next to the result of a call that
already ran, so it cannot stop anything and every failure exits 0 silently. Its
claim directories are separate from `xray-shown-<session>/`; a file's
`xray-read-<session>/` claim is what tells the edit tripwire the edit is not blind. Local: the checkout
and the graph cache, identical in remote mode; the one brain request is the X3 scar lookup, and a failure
there is no scars.

**The brief (X19) is the same map, asked for.** For an agent that fires neither
hook, `code_xray` `action: "brief"` (or `gingugu xray brief`) runs
`xray/brief.answer` on the caller's machine:

```
changed files: git diff --name-status over --range, the named --files, or the
  working tree against HEAD plus untracked files
  → graph: xray/hook_common cache hook-sessions/xray-graph-<sha16(root)>.json
    (the one the hooks share; rebuilt locally on a fingerprint miss, re-parsing
    only files whose bytes changed: xray-parse cache, X16)
  → rows: path, status, rank, importers; blast of the changed source files
    (code + candidate tests); repo top-5
  → docs (X22, MEMORY_XRAY_DOCS): docs_map.read → Docs citing the changed files: /
    Docs still citing a deleted or renamed file: (D and R old paths); a failure
    drops the sections, never the brief
  → data (incl. `docs`, `docs_gone`) + compact `text` (<= 8800 chars)
```

**The brief rides a board post (X20).** A post carries only a ref, a line of its
own `xray: <range>`. The receiver renders the brief from its own checkout:

```
sender: board post with no `xray:` line, from a branch ahead of the default branch
  → pre_tool_tripwire.py passes the board post (main thread too)
  → gingugu hook tool → board_nudge.check → deny once per session with the exact line
post body → board_brief.ref_in: first `xray:` line, ref alone on it, <= 200 chars,
  brief._check_range gate (else no brief)
  → attach(body, cwd): MEMORY_XRAY_BOARD off → nothing
    `xray: <repo> <range>` → repo_id.find: cwd checkout if it matches, else exactly
      <root>/<name> under MEMORY_XRAY_REPOS / default folders, origin-verified;
      none → one line naming the repo; found → the brief from that checkout
    no cwd / not a repo → one line: not available here, run `gingugu xray brief --range <ref>`
      (the range enters that command only when shell-safe, else `<range>`)
    unknown ref → one short line; else brief.answer(cwd, ref) text
  → prompt hook mail: appended to the block for the first of the shown posts (<= 5)
    that names a range, one per prompt, <= 3500 chars (a cut ends with
    board_brief.CLIPPED); the range is checked by git
    (10s deadline) before any graph build, and a build the hook killed is not retried
    in that session
  → board watch: `xray` field + appended to `text` + GINGUGU_POST_XRAY,
    <= 3 posts per batch, from the watcher's cwd (or the named repo's checkout)
```

For `code_xray` `brief` (X19), nothing is stored and the brain is never asked: stdio
answers it in process, the proxy claims it and answers it locally, and `gingugu serve`
refuses it. The board brief (X20) uses no tool call at all: it runs in the prompt hook
or the watch process on the receiver's machine.

**The symbol graph (X17) rides the same build.** Every adapter's parse (Python, TS/JS,
Go) carries its walker's records (`"sym"` in each cached raw); each adapter's linker
resolves them into call edges, `extract.py` joins the shares with `symbols.merge`, and the build writes `xray-symbols-<sha16(root)>.json` beside the graph cache,
same fingerprint:

```
raw "sym" per adapter (python symbols_walk, ts_symbols_walk, go_symbols_walk:
  defs, binds, calls, types; cached by content, X16)
  → each adapter's link (symbols.link, ts_symbols.link, go_symbols.link; TS follows
    barrels via ts_exports, Go scopes names by package): scoped defs → import
    bindings → typed variables in scope → self/cls/this; each call resolved |
    external | unresolved
  → symbols.merge of every adapter's Symbols (extract.py)
  → hook-sessions/xray-symbols-<sha16(root)>.json (never in the brain payload)
  → edit tripwire: old_string → line span → symbols.enclosing → symbols.callers
  → read neighbourhood: distinct direct callers per definition, top 3
  → code_xray callers: symbols.callers for one (file, symbol), stdio/proxy only
```

X4 and X6 read git history against the same graph cache, locally:

```
code_xray history | blindspots (stdio handler, or the proxy; serve refuses)
  → history_tool.answer: local.root(path) → hook_common.graph (cached by fingerprint)
  → history.read(retry=True): HEAD → xray-history-<sha16(root)>.json if HEAD matches,
    else git log --no-merges -M --name-status -z -n 1000 (10s) → parse (renames
    followed) → summarize (churn, author counts, pairs shared >= 5; changesets > 30
    files skipped for pairs) → cache; a failure writes xray-history-failed-<sha16>
  → history:    hotspots.hotspots (churn x rank x authors) + hotspots.coupling
                (>= 5 revs, degree >= 30%, `hidden` = no direct import; `files` focus)
  → blindspots: hotspots.blindspots (imported, untested by import or name, rank/authors)
  → lists paged by limit (default 50, cap 100), totals whole; `text` <= 8800 chars
```

X22 reads docs against the same file list, locally:

```
code_xray docs (stdio handler, or the proxy; serve refuses)
  → docs_tool.answer: local.root(path) → docs_map.read(root, state)
  → file list: ls-files --cached --others --exclude-standard (10s); this repo's docs =
    files with a doc extension (<= 5000, <= 256 KiB each, <= 1000 path tokens each)
  → xray-docs-<digest>.json (owner-only, untrusted: type-checked, a malformed entry dropped and re-read, the rest kept; ignored whole if over 16 MiB or for another identity) -
    a local doc re-read only when size or mtime changed
  → siblings: git checkouts directly under the MEMORY_XRAY_REPOS roots (<= 64; a
    checkout with this repo's own identity or an invalid one skipped; 3s budget, past it
    a cached entry is kept unchecked or the sibling skipped), tracked docs only, re-read only
    when its HEAD moved; kept: repo-qualified paths (`name/` or `owner/name/`), GitHub links to this origin, and prose
    placing a path in it (links/prose.py); the xray-docs cache carries MATCH_VERSION
  → docs_cites: token (scar_query._TOKEN) → scars._resolve → exactly one repo file, else
    no citation; a doc never cites itself
  → files given: citing docs per file; none: totals + most cited files; `text` <= 8800
```

X23 reads cross-repo links from the same checkouts, locally:

```
code_xray links / gingugu xray links (stdio handler, or the proxy; serve refuses;
  MEMORY_XRAY_LINKS=off refuses); the brief calls links.tool.for_brief (3s budget)
  → index.read(root, state, budget_s): this checkout + sibling checkouts (the docs map's
    discovery: MEMORY_XRAY_REPOS roots / default home folders)
  → per file (<= 8000 per repo, <= 256 KiB; lockfiles, vendored dirs and generated code -
    by name (links/generated.py) or by head marker - skipped; opened
    O_NOFOLLOW|O_NONBLOCK, regular files only):
    provides*.py   -> provided facts: package, image, host, route (unmounted tails), resource;
                      tests and fixtures provide nothing (they still mention)
    mentions*.py + prose.py -> mentioned facts: ci, submodule, repo-dep, dep, image, host,
                      route, name, cite (repo@sha path), doc (prose), repo-name (a repo
                      named outright: bare hyphenated name, owner/name, GitHub URL); <= 2000 facts, 200 names;
                      a name is never read from .env* files, a secret-named line, a known
                      token prefix or a random-looking value (names are cached on disk)
    per repo: repo-name candidates cut to names of a checkout read here (_prune), then
    <= 100,000 facts a side, 20,000 names, 500 loose (globbed/wildcard) names
  (gingugu xray links --warm: the same read with no practical budget; fleet.py upgrade runs
  it after installing on each machine with checkouts)
  → xray-links-<digest>.json per checkout (owner-only; by file size + mtime; keyed to an
    extractor-source digest so any extractor change rebuilds); past the budget a repo keeps
    its cache and reports the files it did not read (read.partial / read.unscanned); past
    it no sibling is asked anything: a cached one is served unchecked
  → join: a mention in B of a name A provides -> link B -> A (kind, evidence file:line,
    certain or flagged); routes certain only beside a host the provider serves; cites
    checked for drift (hex sha and a git-listed path only; git diff --no-ext-diff
    --no-textconv; <= 20 checks in 5s; the brief skips them); private hosts called by
    >= 2 repos and served by none -> shared
  → join: a repo-name mention joins only when it names a checkout read here -> whole-repo
    link (docs / ci / mention) flagged `named`, dropped if the same file links stronger
  → no files: leaning_on_this / this_leans_on (+ totals)
  → files: links into those files through their importers + whole-repo links; ripple.py
    follows them backwards <= 3 hops (a file link counts when its target is touched, widened
    by that repo's import graph - this checkout's built if need be, a sibling's only when
    already cached; pin/deploy count when a change reaches the repo;
    ci/submodule only into the changed repo)
  → render: `text` + caveat; the brief adds `Other repos leaning on these files` and
    `Ripple across repos` (data key `links`); a failure never costs the brief
  → inbound.write_all: per checkout read, xray-links-in-<digest>.json (owner-only): links
    into it by target file (<= 8) + whole-repo links (<= 20)
```

Hooks read that summary and never scan siblings:

```
read hook (PostToolUse Read): map on arrival -> inbound.refresh (summary older than 600s,
    1s read budget; past it siblings come from cache) -> "Other repos lean on this one: N
    links from K checkouts here (...)"; a file's first read -> inbound.file_line ->
    "Other repos lean on it: <repo> <file>:<line> (<kind>)" (3 shown)
edit note (PostToolUse Edit/Write/MultiEdit): any file type -> inbound.file_line, once per
    file per session; header "GINGUGU X-RAY after edit:" when it is the only line
MEMORY_XRAY_LINKS=off silences every line
```

X22's docs map reaches the hooks the same way (`xray/docs_hook.py`): a per-repo summary
`xray-docs-in-<digest>.json` (`{v, at, files: {rel: [[doc, repo|null]]}}`, <= 8 per file,
<= 2000 files) from `docs_map.cited`, never a docs scan per call:

```
read hook (PostToolUse Read): map on arrival -> docs_hook.refresh (summary older than 600s:
    docs_map.read(budget_s=1.0), the budget checked per sibling doc) -> no map line;
    a file's first read -> docs_hook.file_line -> "Docs cite it: a, repo:b (+N more) -
    code_xray docs names them all." (3 shown)
edit note (PostToolUse Edit/Write/MultiEdit): any file type -> docs_hook.file_line,
    claim xray-docs-edit (once per file per session)
Bash X-ray (PostToolUse Bash): changed source -> "<rel>: Docs cite it: ..."; deleted ->
    "<rel> was deleted. Docs still cite it: ..."; same claim, 3 named, the rest counted
    and left unclaimed; never refreshes (no summary = no line)
MEMORY_XRAY_DOCS=off silences every line and writes no summary
```

**Env vars as edges (X7a, `xray/env_tool.py`).** `code_xray` `env` / `gingugu xray env`
read `env_map.read` (no budget: every handled file) and answer locally:

```
files given -> per file: reads [{name, set_in: [path:line]}], sets [{name, read_by: [...]}]
no files    -> every name {reads, sets, flag}: 'never set here' first, then 'never read
               here', then by name; limit capped at 200; text <= 8800 chars
both        -> unread: up to 10 files too big to read or cut at the name cap; while any
               exist the files mode says 'set in no file read' / 'read by no file read', the
               no-files flags become 'not set / not read in any file read', and
               totals.never_set / never_read count 0
MEMORY_XRAY_ENV=off -> ValueError; gingugu serve -> refused (the brain never reads a path)
```

X7a's env map reaches the hooks without a summary (it is this repo only, cached per file):

```
read hook (PostToolUse Read): a file's first read -> env_hook.file_line -> env_map.read
    (re-scans only files whose size/mtime changed) -> "Env: reads A (set in f:N), B (set
    nowhere here); sets C (read by g:M)." (4 names, 2 locations each, "(+N more; code_xray
    env has them all)")
edit note (PostToolUse Edit/Write/MultiEdit): env_hook.edit_lines -> names read in the new
    text (scan_text) minus those in the old text, set nowhere in env_map.read ->
    "- Env: reads NAME, set nowhere in this repo (...)", claim xray-env-edit per name per
    session, 3 at most
both: env_map.read(budget_s=1.0); a file not reached keeps its cached entry or waits for
    the next call (the cache is written either way, so it converges); EnvMap.complete is
    False while any file is not yet reached, and both hooks then say nothing ("set nowhere" needs
    every file); a file no reader handles (env_scan.kind is None) skips the scan
unread (EnvMap.unread: a file too big to read or cut at the name cap): the read hook says
    "X (not set in any file read; <file> is too big)" instead of "set nowhere here" (and
    "not read" for "read nowhere"), the edit note stays silent, and code_xray env flags
    'not set / not read in any file read' and lists the unread files
MEMORY_XRAY_ENV=off silences every line
```

**DB tables as edges (X7c, `xray/table_tool.py`).** `code_xray` `tables` / `gingugu xray
tables` read `table_map.read` (no budget: every handled file) and answer locally:

```
files given -> per file: defines [{name, used_by: [...]}], drops [{name, used_by: [...]}],
               uses [{name, defined_in: [path:line], dropped_in: [path:line]}]
no files    -> every table {name, defines, uses, files, flag} (counts): 'defined nowhere here'
               (or 'not in the files read' while any file is unread), 'dropped, still used',
               'used nowhere here' first, then by name, 'tests only' last; totals {tables,
               defined_nowhere, dropped_still_used, used_nowhere, tests_only};
               limit capped at 200; text <= 8800 chars
both        -> unread: files too big to read or cut at the 200-name cap (TableMap.unread);
               while any exist nothing is called 'defined nowhere', and the text names them
MEMORY_XRAY_TABLES=off -> ValueError; gingugu serve -> refused (the brain never reads a path)
```

The table map reaches the hooks the same way the env map does (this repo only, cached per file):

```
read hook (PostToolUse Read): a file's first read -> table_hook.file_line -> table_map.read
    -> "Tables: defines A (used by x.py, y.py +N); uses B (defined in f:N), C (dropped in
    g:M), D (defined nowhere here)" (defines first, 4 names at most, the file's own tables
    not repeated as uses, code users before tests); while a file is unread, D reads
    "D (not in the files read; <file> is too big)" and the edit note never says
    "defined nowhere"
edit note (PostToolUse Edit/Write/MultiEdit): table_hook.edit_lines -> tables in the new
    text minus those in the old -> a use of a table defined nowhere here (only when the repo
    defines at least one) or dropped here; an edit to a definition other files use
    ("defines x, used by K other files"); a drop of a table other files still use;
    claim xray-tables-edit per table per session, 3 at most
both: table_map.read(budget_s=1.0); a file not reached keeps its cached entry or waits for
    the next call; TableMap.complete is False while any file is unread, and both hooks then
    say nothing; a file no reader handles skips the scan
MEMORY_XRAY_TABLES=off silences every line
```

**Attack-surface map (X7d, `xray/surface_tool.py`).** Built from the hook symbol cache
(`hook_common.symbols`) and the X7c table map; the brain is never asked:

```
parse (per file, content only): symbol records + entries [scope, label, form, x, y, line,
    start, end, method, route, recv] + sql [scope, line]   -> X16 parse cache, `valid` re-checks the shape
link (per language): every call verdict -> surface_link.Collector
    Python link also: pyproject scripts/gui-scripts/entry-points `mod:func` -> cli entry
    external call in the catalogue -> Sink (path, caller, name, line); other external -> dropped
    sql record -> Sink 'sql.built' where it sits
    entry: 'd' own definition | 'n'/'a'/'s' handler resolved like a call of that form
           (an http registrar's handler is taken after its path; a bare `path(x)` needs a literal)
           | 'i' Python `if __name__ == "__main__":` block, label `__main__`, kind main
           | 'i' inline callback: start nodes = calls inside its span, direct sinks kept
    -> Symbols.sinks / Symbols.entries -> xray-symbols-<digest>.json (symbols_model.from_json:
       catalogue names, identifier labels and the brain's path rule only)
mounts (cross-file): `mounts` record -> surface_mounts -> prefixes per router; entry
    method/route/name from surface_route*, one entry per URL (max 8, {prefix} when runtime)
answer: surface.build(sym, db_sinks(sym, table_map.uses), skip=is_test)
    db sink = X7c use joined to the innermost definition enclosing its line
    per entry: BFS forward over call edges -> shortest path to each sink node; hops =
        calls from the handler (inline callback's callees = 1); sorted by category order
        exec, eval, deserialize, sql, fs, secret, net, db, then hops
    entries sorted by worst reach then path; then sinks no entry reaches
files given -> entries in them or whose reach lands in them; unreached sinks in them
output      -> entries [{path, line, label, method, route, name, kind, handler, inline, resolved, reach: [{sink,
               category, path, symbol, line, hops, via (<= 6 names), via_cut}] (20 at most),
               reach_total}],
               unreached, totals {entries, reaching, unresolved, sinks, unreached}; limit
               capped at 200; text <= 8800 chars, 3 sinks shown per entry; caveat
MEMORY_XRAY_TABLES=off -> no db sinks; gingugu serve -> refused (the brain never reads a path)
```

**X7d in the hooks (xray/surface_hook).** The same map, on arrival and after an edit:

```
read hook (first Read of a file, inside the xray-read claim)
    -> hook_common.symbols(fp) + table_map.read(budget 1s) -> surface.build
    -> "Surface: entry points here: <kind name> (L) -> <category sink>, <hops>[, +N more sinks]"
       for entries in the file that reach a sink, most dangerous first, 3 shown
    -> "Surface: sinks here reached from entry points: <category sink> (L, `symbol`) from
       N entries: <names>" for sinks in the file reached from entries elsewhere
    -> "(+N more; code_xray surface has them all)"; db included; dot-directories included
edit note (Edit / MultiEdit / Write of a file in an X-ray language)
    before = surface_hook.before: hook_common.cached_symbols + table_map.cached_uses,
             taken before any other line refreshes a cache
    after  = hook_common.symbols (fingerprint-checked; parse cache re-parses the edited file)
    diff   -> "this edit adds entry point ..."                (a new entry in the file)
              "this edit makes <sink> (path:line, `symbol`) reachable from <entry> (hops: via)"
              "`f` is on a path from <entry> to <sink>"        (a definition the edit landed in)
    claims xray-surface-edit (each fact once per session)
    rebuild raises -> "the after-edit reach check was skipped ..." (claim xray-surface-skip)
MEMORY_XRAY_SURFACE=off -> no surface lines; any failure -> no line, never an error
```

**Readers of a removed key (xray/literals, board d5639dd5).** The edit note also
runs for any file type, on Edit/MultiEdit only (a Write has no old text):

```
edit_note.check → literals.lines(payload, root, rel, state_dir, session)
  → quoted literals: "x"/'x' in an old_string no new_string still quotes
  → unquoted keys: Python kwarg `x=` (.py), TS/JS object key `x:` (.ts .tsx .js .jsx
    .mjs .cjs) in an old_string no new_string still names
  → qualifies (3-80 chars, identifier-shaped, not a STOP value); at most 3 per edit
  → one `git grep -n -I -F --untracked` per key, 1s timeout; failure drops the line
  → boundary check in Python: "x" 'x' `x` @x anywhere, `x:` outside non-test code; unquoted
    keys also `x=` / `.x`, in tests, fixtures, docs and non-code only (symbol graph
    covers other code); skips the edited file, generated files, lockfiles
  → once per (session, repo, key): hook-sessions/xray-literal-<session>/
  → "- Text matches, not import edges: `x` removed by this edit is still named in N
    files - path:line, ... (+K more)"; over 40 files: "is named in N files - too
    common to list"; an unquoted key adds " (unquoted key)" after `x` and
    " (read outside non-test code)" at the end; "file" when N is 1
MEMORY_XRAY_TRIPWIRE=off silences it
```

The hooks hash the checkout once and hand that fingerprint to both cache reads.

**After an edit, and the test ledger.** The note after an edit and the ledger that
scores it:

```
PostToolUse Edit|Write|MultiEdit → post_edit_xray.py → gingugu hook edit
  → xray/edit_note.check: source file in a repo (any language X-ray parses), MEMORY_XRAY_TRIPWIRE on
      claim hook-sessions/xray-read-<session>/ for the file (seen: no blind deny later)
      Write → stop; Edit/MultiEdit → each new_string's start line in the file now
      symbols: cached xray-symbols-<sha16(root)>.json, fingerprint NOT checked (no
        rebuild; callers of a definition do not change with its body)
      → symbols.enclosing → symbols.callers → cross-file direct callers, tests in
        reach (hook_common.nearest_tests, seeded with the file; lang.is_test per
        language, a test named for the file first: test_x.py, x_test.py, x_test.go,
        x.test.ts, x.spec.tsx)
      → once per (session, file, definition): hook-sessions/xray-edit-<session>/
      → ledger.predict: kind calls (those tests) and kind imports (import-blast
        nearest tests from the cached graph)
PostToolUse Bash → gingugu hook bash → xray/ledger.observe (independent of the X15
  snapshot): a test command (ledger_runs: pytest, vitest, jest, npm/pnpm/yarn/bun
  test, go test, gotestsum), not interrupted → failed test files from each
  runner's summary lines in tool_response stdout + stderr (ANSI stripped; go: the
  failing package import path + test-file basename; absolute, `..`, `_/<dir>` and
  >512-char paths dropped) → outcome row; a failed path matches a predicted one
  when equal or one ends with the other on a `/` boundary
gingugu xray score → predictions before each outcome in the same (session, repo)
  → per predictor: top5 | listed | missed; no prediction in that session: unpredicted
```

## X-ray of a shell command's writes (X15)

```
PreToolUse event (Bash)  →  pre_tool_tripwire.py (already passes Bash)
  → `gingugu hook tool` → xray/tool_xray.reason → xray/bash_hook.before
      MEMORY_XRAY_TRIPWIRE=off, no session id / tool_use_id / command: skip
      candidate checkouts, at most 8, in order:
        the command's cwd repo (recorded as touched)
        every repo this session touched: hook-sessions/xray-repos-<session>/,
          one owner-only record per repo, written by the read hook, the edit
          hook and this hook, most recent first
        the checkout of every existing path token in the command (first 8 KiB,
          <= 64 tokens; stops at the cap or the 4s deadline)
      per candidate, until the 4s budget runs out:
        dirty set = git status --porcelain=v1 -z --untracked-files=all
          {path: [status, sha256 of the file if <= 64 KiB else size:mtime_ns]}
          (a symlink is compared by where it points, never followed)
          more than 500 dirty paths, or git failing → skip that repo
      → hook-sessions/xray-bash-<session>/ snapshot keyed by tool_use_id (owner-only)
      → never denies, prints nothing

PostToolUse event (Bash)  →  .claude/hooks/post_bash_xray.py (pure stdlib; settings 15s, its child 10s)
  → `gingugu hook bash` (post_tool_hook) → xray/bash_hook.after
      MEMORY_XRAY_TRIPWIRE=off or no snapshot: skip; the snapshot is consumed (deleted)
      the diff has a 6s budget across repos
      per snapshotted repo: dirty set again; a source file (lang.for_path) whose
        hash differs, appeared, or vanished is changed or deleted
      deleted file → its importers from hook_common.cached_graph (the cache as it
        stood before the command), minus the other deleted files
      changed files → hook_common.graph (rebuilt locally on a fingerprint miss, re-parsing only files
        whose bytes changed: xray-parse cache, X16), then
        per file the edit tripwire's blast lines (direct importers, nearest tests)
      a file already claimed in xray-shown-<session>/ is skipped, else claimed
        (so the edit tripwire does not deny it later); at most 5 files in full,
        the rest counted ("+N more changed source files not shown")
  → additionalContext "GINGUGU X-RAY: that command changed source in <repo>: ..."
    (<= 8800 chars), beside the tool result
```

**Snapshot, then diff; inform, never block.** The write already happened by the time
the result lands, so the text can only point at callers to check. The claim is the
edit tripwire's own `xray-shown` one, taken from both sides, so a file reaches the
agent once per session whichever tool changed it. Entirely local, identical in remote
mode. Not covered: a repo the session never touched, reached only by a `cd` inside a
script.

## Warm minions (SubagentStart + the minion fence)

```
Agent tool call (main thread)  →  PreToolUse  →  `gingugu hook tool`
  → no agent_id + tool Agent/Task: stash tool_input.prompt keyed by
    (session_id, subagent_type) in hook-sessions/spawns-<session_id>.json
    (300s TTL, 0600, stale files swept, unsafe session ids hashed) - only when
    the spawned agent's file carries a grant; never blocks the spawn

subagent starts  →  SubagentStart  →  .claude/hooks/subagent_warmup.py
  → `gingugu hook subagent`
      → take the oldest stashed prompt for (session, agent_type) = task_hint
      → find the agent file whose frontmatter `name` is agent_type
        ($CLAUDE_PROJECT_DIR, then ~/.claude/agents); read MEMORY_GRANT from
        its frontmatter. No grant → exit silently (no warm-up)
      → in a folder Claude Code has not trusted, a project agent file's
        inline brain will not load: the warm-up says so (claude_trust)
      → narrow_spec: keep only entries the client's grant can read
        (a * entry kept); nothing left, or a client grant that cannot be
        honoured → exit silently
      → read-only SQLite, grants.bind(grant): build_context per readable
        namespace (encoder only if there is a hint), de-duplicated
      → additionalContext: compact lines + which namespaces it may write
  → the subagent's own stdio server (inline `mcpServers` in the agent file)
    runs with MEMORY_GRANT: same store chokepoints as a scoped serve token

every subagent tool call  →  PreToolUse (agent_id present)
  → minion_fence.decide (MEMORY_MINION_FENCE=off disables):
      file tools on the data dir, or Grep/Glob rooted
        at or above it                                 → deny  (holds)
      shell naming data dir / memories.db / sqlite3 /
        running the gingugu CLI in command position    → deny  (speed bump)
      inherited mcp__gingugu__ write tools + credential_* → deny
      Agent / Task (a minion spawning a minion)        → deny  (holds)
      anything else                                    → tripwires as usual
```

SubagentStart carries no task prompt, which is why the spawn is stashed one
hook earlier; two parallel spawns of one type can swap hints, which costs
ranking, never the fence (both share one grant). The shell check matches
command text, so prose containing those words (a heredoc, an echo) can be
denied too - a known speed-bump edge. Threat model: accidents, not a hostile
same-user process.

## Hooks in remote mode

With a remote brain on (`hook_remote.target()`, i.e. `remote.active()`), each
hook keeps its local half and asks the brain for the rest. Every request is a
POST with the machine's owner token from the keychain. An unknown token is a
401 (the middleware), any other token kind a 403, a malformed or oversized body
a 400, and an unexpected failure a 500.

```
UserPromptSubmit → `gingugu hook prompt`
  → board mail (persona agents, every prompt, before the gate):
    POST /hook/inbox {persona, namespaces (grant names, null = unfenced),
    shown} -> {posts}; 3s timeout; brain: read-only grant-bound inbox query
    for persona + origin (the calling owner token's name, never the body; a
    twin persona on another machine's posts count as mail, and so do a sibling
    session's on the same machine when both sessions are known; the writing
    session never gets its own), minus the shown ids; shown ids stay in local hook-sessions/ under
    board:<session>. The body also carries agent_session (the raw Claude Code
    session id, hashed by the brain): a post routed to another session of the
    persona is held back until it has gone unread for ROUTE_GRACE_SECONDS (1h). A persona agent's one-liner therefore makes this request
  → strip_notifications, then the length gate (local; no recall request for a
    one-liner or a harness notice)
  → namespaces_for(cwd), narrowed by the client's grant ([] → exit), then _sendable
    drops any name the routes would refuse
  → POST /hook/recall {prompt, session_id, namespaces, suppressed, config}
      brain: strip_notifications → length gate (an older client may send a
             notice) → strip_affect → embed (model held warm) → sweep + lexical → select
             → query_log row (tool='hook') → {context, ids}
  → emit context; add ids to local hook-sessions/<session>.json

PreToolUse → `gingugu hook tool`
  → minion fence + spawn stash (local, unchanged)
  → X-ray edit check and the X15 Bash snapshot: local (checkout, graph
    cache, tripped state); the one request to the brain is the X3 scar lookup,
    POST /hook/scars {namespace, basename} -> {scars} (3s; any failure = no scars)
  → rules: hook-sessions/tripwire-rules-<sha(url, namespaces)>.json if < 60s old,
           else POST /hook/tripwires {namespaces}; on failure the stale copy
           (namespaces grant-narrowed and _sendable-filtered, as for recall)
  → match locally; suppression in local tripwires-<session>.json
  → on a trip: POST /hook/trip {session_id, text, ids, namespaces}
      brain: query_log row (tool='tripwire')

PostToolUse → `gingugu hook read` / `gingugu hook bash` / `gingugu hook edit`
  → X-ray map on arrival, the X-ray of a shell command's writes (X15) plus the test
    ledger's test-runner outcomes, and the note after an edit: local (checkout,
    caches, claim dirs, ledger); the only request to the brain is the X3 scar
    lookup on a Read, POST /hook/scars, so the rest is identical in remote mode

SubagentStart → `gingugu hook subagent`
  → take the stashed task + read the agent file's grant (local), narrowed to
    the client's grant
  → POST /hook/warmup {spec, task_hint, agent_type, namespaces}
      brain: same warm-up, wildcard grants scoped to the sent namespaces
```

The brain keeps no session state: suppression travels with the request, so a
brain restart costs nothing and session ids never leave the machine except as
`query_log` labels. Route work runs in a worker thread: reads on its own
read-only connection, the only writes the `query_log` rows. Any failure -
unreachable brain, no keychain token, a non-200, a reply of the wrong shape -
is a quiet hook and never opens the local DB. Tripwires are the one exception
to quiet: they keep matching the last rules they fetched, however old, until a
fetch succeeds. A trip's text is cut to 2000 characters before it is sent.

## Explorer read path (observer)

```
browser → gingugu ui (webui.py, loopback bind + loopback Host only)
  → unknown /api route? 404 (allowlist, before anything is forwarded)
  → remote.active() per request:
      local : ui_api.dispatch(path, params, env)  on a mode=ro connection
      remote: webui_remote.forward → GET <brain>/ui/api/*  (owner token, pinned TLS,
              no redirects) → serve_ui (owner-only) → ui_api.dispatch on the brain
  → JSON body stamped `mode`  (/api/info also names the brain and the viewer's persona)
```

Client loop: the page polls `/api/info` every 10s. `/api/info` and `/api/graph`
both carry `graph_version`; the page refetches `/graph` only when it changes, so
an idle brain costs one small request per tick. A memory's detail is fetched on
demand from `/memory/<id>`. The URL mirrors state as `?view=<tab>&m=<id>`. In
the static demo there is no server: when `/api/info` fails on a static host the page loads the bundled
`graph.json`/`info.json`/`details.json` (written by `ui/dump_static.py --db`
from the same `ui_api.dispatch`) and does not poll.

Routes: `/info`, `/graph` (light nodes without bodies, relations, IDF tag
edges), `/memory/<id>` (body, tags, relations in and out, claims, tripwires,
access history), `/search` (`search.search()` with the score breakdown),
`/stats`, `/events` (`access_log` and `query_log` merged, cursor `after`,
replay by `from`/`to`; a page never splits a same-timestamp batch).

The path is read-only end to end and **does not credit recall**: no
`access.record` or `touch`, no `query_log` row, no spreading activation, and
`/stats` skips the `access_log` prune. The connection is `mode=ro` and never
migrates or creates a database, so opening the Explorer cannot change the brain
it shows. The keychain token exists only in the forwarder's `Authorization`
header.

## Relations + spreading activation

```
memory_relate(source_id, target_id, relation_type)
  | edges[]                                         (all-or-nothing, one transaction)
  → relations.py writes a directed typed edge
  → later recall/context traverse edges so one hit surfaces its cluster

dampened_neighbour_ids(seeds)                       → who wakes, and who is shown
  per seed, grouped per NEIGHBOUR (not per edge — a pair joined by several
  edges is one candidate, scored by its strongest), ranked by:
    confidence rank ▸ RELATION_WEIGHT ▸ low degree ▸ recency ▸ id
  ≤ SPREAD_PER_SEED (3) per seed, ≤ SPREAD_TOTAL (10) overall, seeds excluded
  → _collect_related   → via_relation extras in the payload
  → _spread_activation → touch_many, resets the dormancy clock (no access credit)

memory_edges(namespace|relation_type|memory_id)     → read the graph
memory_unrelate(... new_relation_type? reverse?)    → repair it
  | edges[]
memory_stats.graph.orphan_sample                    → what the graph never reaches
memory_search(orphans=True)                         → the same set, with bodies
```

Edges are the load-bearing structure, but recall quality scales with edge
**precision**, not edge count. Hybrid retrieval already ranks by text and
semantic similarity, so a `related_to` edge meaning "these are about the same
topic" duplicates the index for free while competing for a traversal slot
against an edge that records what search cannot infer: what a memory
`supersedes`, `contradicts`, was `caused_by`, or belongs under. Store-then-relate
is still the expected loop; the filter is whether you can name the directional
fact the edge records.

Measured on the live brain 2026-08-13 via the new `memory_stats.graph` block:
61% of 1,570 edges are `related_to` and 339 memories carry more edges than
traversal will ever visit - the cost of the older volume framing, still being
worked off.

Edges are repairable, which is what makes that precision demand fair: a surface
that insists on picking a specific type and then makes every wrong pick
permanent charges the full cost of an error on every future recall.
`memory_edges` reads the graph (both endpoints resolved to titles, namespaces
and degree, so an edge can be judged from the row) and `memory_unrelate`
repairs it - retype in place, reverse, or delete. Retype UPDATEs the row rather
than recreating it, so id, `created_at` and metadata survive: the usual repair
is "right connection, wrong label", and when the link was drawn stays true.
Retyping onto a type that already joins the pair collapses the two and reports
`merged`, not `retyped` - the edge count genuinely drops by one.

`reverse` is the sibling repair: the pair is right and the arrow points the
wrong way. `A caused_by B` written for `B caused_by A` is a false claim about
causality, not an untidy one, and the directional types this graph asks for are
exactly the ones that can be recorded backwards. It swaps the endpoints on the
same row, with the same provenance guarantee, and combines with a retype in one
write because an edge recorded backwards is frequently mislabelled too.
Reversing `parent_of`/`child_of` is the same operation as flipping between
them - do one or the other, not both.

Batches are lists of per-edge decisions, deliberately **not** criteria-driven
sweeps. There is no "retype every `related_to` here" option, because each edge
deserves a different type based on what it records; a blanket relabel would
manufacture directional claims that were never true, and a false `caused_by`
retrieves worse than an honest `related_to`. The batch is validated whole before
anything is written, so a bad op cannot leave the graph half-repaired.

Traversal is hub-dampened (`RelationManager.dampened_neighbour_ids`): the same
budgeted set powers `include_related` extras and spreading activation, so a
highly-connected "generic hub" memory contributes its few best neighbours
instead of its entire cluster. Budgets (3 per seed, 10 total) are tuned against
the real-brain benchmark.

The other end of the same problem is memories the graph never reaches at all.
`memory_stats.graph` reports an orphan count, and `orphan_sample` names the
memories behind it - ordered by confidence, then access count, then recency, so
the orphans costing the most retrieval come first, each row carrying its
namespace. `memory_search(orphans=True)` pulls the same set with full bodies,
composing with every other filter, with or without a query.
`graph_stats.orphan_filter()` is the single definition behind both, on the same
argument as `claim_filter()`: a count and its enumeration must be counting the
same thing. Deprecated orphans sink to the bottom of the sample rather than
being filtered out, so - unlike claims, which needed `open` and
`open_actionable` - one population serves both numbers and no gap needs
explaining. Reconnecting an orphan is still a judged act: an orphan is better
left alone than wired up with an invented edge.

## Dream (deterministic consolidation)

Runs on command (`memory_dream(action="run")`) or unattended
(`gingugu dream --if-idle`, on an OS timer). Reads the graph; writes only to
`proposals`.

**The guards, before any of it happens:**

```
gingugu dream --if-idle[=MINUTES]        <- cron/launchd/Task Scheduler fires this
  -> activity.is_idle_for(conn, threshold)
       reads ONE row: activity.last_active_at
       stamped by every MCP tool call, in a finally  <- errors count as use too
       unknown (no row / unparseable) -> NOT idle    <- no evidence != absence
       future stamp (clock skew)      -> clamped 0   <- reads as busy
     not idle -> print, exit 0, ~0.42s               <- the common case
  -> dream_lock.acquire(conn)                        <- BEGIN IMMEDIATE, one winner
       live holder -> exit 0, "already running"
       expired / unparseable expiry -> take it over  <- a crash costs one window
  -> embedder_factory()                              <- ONLY built past both guards
  -> dream.run(..., should_continue=is_idle_for)
       checked BETWEEN passes, same threshold as the gate
       user returns -> stop, keep what finished, report cancelled
  -> dream_lock.release(token)                       <- token identifies the LEASE
```

The hand-run paths (`memory_dream(action="run")`, bare `gingugu dream`) skip the
idle gate entirely - asking for a run in a session _is_ the intent - but still
take the lock, because two passes computing the same PageRank is waste whoever
asked for it.

**The pass itself:**

```
gingugu dream [namespace]
  -> dream.graph.load(conn, namespace_id)
       memories in scope, sorted by id           <- stable order = reproducible
       relations where BOTH endpoints in scope   <- no half-dangling inflation
       adjacency treated as UNDIRECTED, matching spreading activation
       tags per node + corpus tag frequency      <- the one when-independent signal
  -> one graph, shared by all three passes       <- so findings are comparable
  -> centrality.find:  pagerank -> >= 3x the 1/N baseline -> drop pinned -> top 10
  -> clusters.find:    propagate -> size 3..40 -> density >= 0.5
                         -> tag_score = coverage x IDF, date-shaped tags dropped
                         -> tag_gap == 0 ? SKIP (accepting could apply nothing)
                         -> sort by tag_score, then density -> top 15
  -> orphans.find:     for each of <=25 orphans:
                         search (candidates) -> payload_similarity (absolute)
                         -> RELATION_MIN_SIMILARITY[basis] floor -> best match
  -> ProposalQueue.stage(kind, subject, object?, score, evidence)
       new              -> INSERT
       pending          -> UPDATE score/evidence   <- the graph moved
       already decided  -> NO-OP                   <- never nag
  -> report: graph shape, found vs staged per pass, queue depth
```

A pass that raises is logged and skipped; the run keeps the others. This is
scheduled work with nobody watching, and losing two good passes to a third's
edge case is the worst available trade.

**Deciding, and where the judgment enters:**

```
memory_dream(action="accept", proposal_id, [relation_type | tag])
  -> kind == "edge"    : relation_type REQUIRED -> RelationManager.relate
  -> kind == "cluster" : tag REQUIRED           -> store.add_tags(each member)
  -> kind == "core"    :                           store.update(pinned=True)
  -> ProposalQueue.decide(id, accepted)
memory_dream(action="reject", proposal_id)
  -> decide(id, rejected). Row KEPT: it is what suppresses the next run's
     identical finding, and the precedent governance bands will later count.
```

The write happens in the handler, through the ordinary managers - never from
inside the queue. So there is no path by which a staged row reaches the brain
without a person passing through the accept step first.

## Lifecycle

- `memory_update` — mutate an existing memory (re-runs hint checks on title/content
  change). Also retypes: `type` is the fix for a misfiled memory, since `pattern`
  and `preference` are exempt from gated review hints. Retyping does not re-embed.
  Also declares `provenance` and `about` (`""` clears either); changing `about`
  does re-embed, since it is part of the text the vector is built from.
  Neither advances `last_confirmed`.
- `memory_forget` — deprecate, or hard-delete. Nothing is auto-forgotten. A hard
  delete takes `confirm` first (see Destructive confirmations below) and cascades
  to the memory's `tripwires` rows.
- `memory_consolidate` - merge / summarize / deduplicate a cluster; without
  `memory_ids`, a read-only suggest scan surfaces near-dupe clusters
  (pairwise embedding cosine, title-only fallback) to feed back in.
  `keep_originals=False` hard-deletes the originals and takes `confirm` first.
- `memory_export` / `memory_import` — back up or transfer a namespace (export
  before any large destructive op). `memory_import` embeds what it writes and
  reports `embeddings_written`; without that, restored memories were reachable
  by keyword only, because FTS5 has triggers and `memory_embeddings` does not.
  Vectors are recomputed on arrival rather than carried in the payload: they
  are model-specific, so a 384-dim export restored on a 768-dim host would be
  discarded anyway, and they are derived from the text sitting beside them.
  Encoding happens **after** the commit, so a failing model costs the vectors,
  never the restore. `on_conflict="replace"` that would overwrite existing ids
  takes `confirm` first (a dialog names how many; over 10 it is a big delete). The
  hook counts the payload's memories, the server the ids it would overwrite.
- `memory_namespaces` `delete` with `cascade=True` on a non-empty namespace takes
  `confirm` first and is a big delete (a dialog names how many memories go with
  it). The hook asks about ANY `delete`, even an empty namespace; the server's
  dialog and approval apply only to the non-empty cascade.
- `decay.py` — recomputes dormancy as a resting signal; never mutates confidence.

### Destructive confirmations

```
memory_forget(hard_delete=True) / memory_namespaces(delete, cascade=True) /
memory_consolidate(keep_originals=False) / memory_import(on_conflict="replace") /
credential_delete
  → resolver (confirm.py; runs BEFORE the tool wrapper, so it checks the fence
    itself): not destructive, or nothing to lose (id absent or out of grant,
    namespace empty, no id overwritten) → no confirmation needed
  → confirm=false → refuse; confirm=true → the caller's yes (BY_CALLER), no dialog
      and, if destructive.gate says this is a BIG delete (namespace delete,
      credential_delete, consolidate/import over 10, hard deletes past 10 this
      session), it also needs the person:
        hook (tool_hook -> destructive.hook_check) answered `ask` → Claude
        Code's permission prompt (any mode; a deny where nobody can answer) and
        left a single-use approval (15 min) under hook-sessions/approvals
        → order in confirm._big: no gate → pass; MEMORY_CONFIRM_DESTRUCTIVE=off
          on the executing server → pass; over HTTP only the owner (full grant)
          passes on confirm=true (its proxy took the approval), a scoped token gets
          a dialog if its client can ask, else "needs the user's own approval";
          on stdio destructive.take consumes the approval → proceed, none → a
          dialog if can_ask, else the same refusal
        → remote mode: proxy_gate.screen takes it before relaying to the brain
        → hard deletes past 10 a session: the hook's `ask` only; nothing here
          consumes an approval and the proxy does not screen memory_forget
  → confirm omitted (the old path):
      MEMORY_CONFIRM_DESTRUCTIVE=off on this server → proceed (it never stops the
      hook's `ask`; the proxy reads it too for its own screen)
      client declared no elicitation, or is a stdio client known to show no
      dialog (CLAUDE_CODE_ENTRYPOINT=claude-vscode; ignored over HTTP), or is the
      proxy's brain behind a withheld `initialize` → refuse: ask the user and call
      again with confirm=true
      else Elicit(Confirm): the client shows the user a dialog
  → body: confirm.refusal(answer, what) → proceed only on a dialog accept with
    confirm=true, or BY_CALLER / OPTED_OUT; decline, cancel (a headless client)
    and confirm=false are `ok: false`
```

The dialog's answer is a hidden parameter, not in the tool's input schema, so the
model cannot supply it; `confirm` is the model's own say-so, which is why a big
delete also needs the hook's approval. Each body re-derives whether the call is destructive and checks
existence before it reads the answer, so a hidden id reads "not found".

## Storage / migrations

```
database.py on startup:
  → open SQLite (WAL mode, foreign keys, busy timeout)
  → hand off to migrations.migrate():
      → read PRAGMA user_version
      → if migrations pending: snapshot to <db>.bak-before-vN via conn.backup()
      → apply pending migrations in order (additive by default), from
        migrations/schema.py (structural), migrations/claim_derivation.py
        (re-derives rows from prose that never changed), migrations/runtime.py
        (coordination state), migrations/fields.py (write-time declared
        fields + FTS rebuild), migrations/queries.py (the query log) and
        migrations/tripwires.py (tripwire triggers)
      → ensure FTS5 virtual table + sync triggers exist and match `memories`
```

The snapshot is taken with SQLite's backup API rather than a file copy, because
in WAL mode the newest committed rows live in `<db>-wal` until a checkpoint. It
is best-effort: a failure is logged and the migration still runs.

Every read of `memories` selects `models.MEMORY_COLUMNS`, the single declared
column list, so no query can return a `Memory` with a field silently left at its
default.

A schema change to `memories` MUST update the FTS5 triggers in the same change,
or full-text search silently drifts out of sync.

A migration that adds **derived** data must also populate it, or the feature
ships inert for everyone who already has memories. Migration 005 backfills
claims inline (pure regex, ~210ms for 735 memories, and `user_version` makes it
exactly-once); migration 004 backfills embeddings at *startup* instead because
encoding needs a model download and must stay lazy. See
`.ai/standards/02-database.md` for which strategy applies when.

A migration that changes how derived data is **computed** must re-derive it,
and must decide explicitly what happens to state a user added on top. Migration
007 re-derives every claim through `claim_rederive.py`, which prunes claims the
corrected extractor no longer produces while preserving `resolved_*` — because
the prose did not change, only the extractor improved, and reconciliation work
is manual and unrecoverable. Reusing `claim_sync.sync_claims` here would have
silently reopened every claim the user had reconciled.

Exactly-once cuts both ways: pending work is selected with `current < target`,
so a migration can never re-run on a DB that already passed it, and a bug fixed
in place reaches only DBs that have not got there yet. Repairing the rest needs
a **new** version number — migration 006 exists solely to re-run the claims
backfill for DBs stamped v5 by a build whose 005 did not have one.

## Credentials

```
credential_store / credential_list / credential_delete
  (credential_delete: always a big delete; `confirm` is optional, default omitted:
   confirm=false refuses outright, confirm=true needs the hook's approval; on the
   remote proxy the local vault checks it; a person at a terminal can run
   `gingugu credential list|delete NAME` - the same vault, secret values never printed)
  → credentials.py reads/writes secret values in the OS keychain
  → only non-secret metadata is listed; secret values never touch the DB or logs

credential_get(service, fields)                 (default)
  → secret fields returned as {is_secret, redacted}; the keychain is NOT read
credential_get(service, fields=<one>, into=<abs path>)
  → handler: refused unless ctx.transport == "stdio", exclusive with reveal
  → vault.write_secret: service/field exist, field is secret
  → secret_file.check_target: absolute, not symlink/dir, parent exists
  → keychain read (locked or empty → refused, nothing written)
  → secret_file.write_private: O_NOFOLLOW, fchmod 0600, then write
  → response: path, bytes, mode - never the value
credential_get(service, reveal=true)
  → the value inline, as a deliberate act
```

The one place a secret touches a file is `into`, on purpose: a file the caller
named is how the value reaches a command without passing through the transcript.

## Promotion (local → central)

```
gingugu promote (promote.py — an MCP client, not the server)
  → memory_export(source_ns) from the LOCAL brain (read-only)
  → filter: keep verified, minus episodic/personal tags, minus secret-content
  → memory_export(target_ns) from CENTRAL → collect already-promoted source ids
  → memory_store each fresh one into CENTRAL with a provenance stamp
     (metadata.promoted_from{instance,namespace,id,contributor,promoted_at}
      + `promoted` tag + source="promotion:<ns>")
  → idempotent: re-runs skip ids already present
```

Stage 1 = insert + skip-already-promoted. Stage 2 (consolidate near-dupes into
one canonical memory with `contributors[]`) and Stage 3 (conflict → `contradicts`
edges via an LLM judge) layer on later.

## Release

```
git tag vX.Y.Z → GitHub Actions → build → Trusted Publishing (OIDC) → PyPI
  → GitHub Release auto-cut from CHANGELOG [Unreleased]
```
