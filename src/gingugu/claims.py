"""Extract checkable state claims from memory prose.

A memory that says "PR #10 is open" is making a *claim*. It was true when
written, and the prose is honest history — so the fix for a claim going stale
is never to edit the text. The claim is extracted here as data instead, and
its resolution is recorded alongside it (see ``memory_claims``, migration 005).

That distinction is the whole point. Before this existed, the only way to
record "PR #10 has since merged" was to rewrite the memory or bolt a
``=== STATUS ===`` banner onto it. The corpus grew 160 distinct banner styles
across 37 memories precisely because there was nowhere structured to put it.

Two problems this has to get right, both measured against a 764-memory corpus
before the module was written:

**Refs are not globally unique.** "PR #12" means different objects in
different repos, and memories routinely reference another repo's PRs. So a ref
is qualified by URL first, then by a repo named next to it, then by
``namespace_default`` — the repo a bare ref means in that namespace, which the
one-namespace-per-repo convention makes the namespace's own name.

That default is load-bearing, not a convenience: measured over 764 real
memories, in-text qualification alone yields 26 claims and **zero** usable
contradictions, versus 145 claims and 10 contradictions with it. People write
"PR #20", not "gingugu PR #20", in their own repo's namespace.

The caveat that follows: in a genuinely cross-project namespace, two bare refs
to different repos' PR #12 would key alike. Pass ``namespace_default=None``
there to drop bare refs instead. Contradiction detection additionally requires
both sides to sit in the same namespace, so a mis-key stays contained.

**State is not a clean binary.** "merge HELD" contains both an open and a
resolved word; "doc shipped PR #168" means the PR was *created*, while
"PR #65 SHIPPED" means it merged. ``shipped`` is therefore excluded from the
resolved vocabulary: a missed claim is silent and harmless, a wrong one
teaches the reader to ignore claims entirely.

**A citation is not an assertion.** ``[[PR #10 open: the promotion bridge]]``
is a *link* to a memory named that, not this memory's claim about PR #10.
Measured on a 785-memory corpus, 11 claims came from inside wiki-links and
every one was wrong — 8 of them in a namespace whose default repo was
perfectly correct, so namespace containment does not help here. The worst
case had a memory titled "RESOLVED: internal gateway crashloop" asserting
``#155 open``, purely because it linked to a memory whose title said so.
Wiki-link spans are therefore blanked before extraction, the same instinct as
``_is_quoted``. Nothing is lost by dropping them: when a claim's only state
evidence sits inside a link, the *linked* memory already holds that claim,
correctly keyed.

**A ref without a state is not open.** Until v0.17.0 a ref whose prose asserted
no state was dropped outright, which made it invisible: a memory reading
``PR #1: <url>`` under a "Deliverables" list produced zero claims, so
``claims.open`` said 0 while the memory read as in-flight to a human forever.
The obvious fix — treat a bare ref as open — is wrong, and the corpus says so
loudly. Measured over 1161 memories, 225 refs are named with no asserted state
against 223 real claims, and they overwhelmingly narrate *finished* work:
"Fixed in PR #873", "PR #121 - deployed successfully", "PR #149 - Expanded
CLAUDE.md". Defaulting those to open would have more than doubled the backlog
with history, which is exactly the failure this module already refuses above.

So a state-less ref gets its own state, ``unverified``, meaning only "this
memory names a ref and never says what became of it". It is a browsable index,
not a backlog: it is excluded from ``claims.open``, from the reconciliation
sample, and from contradiction detection, and is enumerated on its own through
``memory_search(claims="unverified")``. Dropping beat guessing; recording
beats dropping, as long as the record does not overstate what the prose said.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Explicit URL wins over everything — it names the repo unambiguously.
_URL = re.compile(
    r"https?://(?:www\.)?(?:github|gitlab)\.com/([\w.-]+(?:/[\w.-]+)*?)/"
    r"(?:pull|-/merge_requests|merge_requests)/(\d+)",
    re.I,
)

_REF = re.compile(
    r"(?:(?P<repo>[A-Za-z][\w.-]{2,30})\s+)?"
    r"\b(?P<kind>PR|MR|pull request|merge request)\s*[#!]?(?P<num>\d+)"
)

# Resolved beats open when both appear for the same ref in one memory: a memory
# titled "PR #174 MERGED" that also narrates "Opened + merged same day" is
# asserting resolution. ``shipped`` is deliberately absent — see module docs.
# The negative lookbehinds matter more than they look: "PR #10, open, NOT
# merged yet" contains the word "merged" and would otherwise read as resolved,
# inverting the claim. Resolved is tested before open, so this is the only
# place that negation can be caught.
# ``superseded`` is excluded for the same reason as ``shipped``: measured on the
# real corpus it mis-read "MR !4 appears redundant/superseded; needs a decision
# (close, or rebase)" as resolved, when that MR is explicitly still open.
_RESOLVED = re.compile(
    r"(?<!not )(?<!not yet )(?<!never )(?<!isn't )(?<!wasn't )"
    r"\b(?:merged|landed|released|closed|abandoned|deleted)\b",
    re.I,
)
_OPEN = re.compile(
    r"\b(?:open|opened|awaiting|pending|unmerged|"
    r"(?:not\s+(?:yet\s+)?|never\s+)merged|"
    r"held|in\s+review|needs\s+(?:review|merge)|still\s+open)\b",
    re.I,
)

# ``[[wiki-link]]`` spans — a pointer to another memory by title, not a claim.
# Spans a newline (``re.S``) because titles wrap in stored prose.
_WIKI = re.compile(r"\[\[.*?\]\]", re.S)

# How far after a ref to look for a state word, and around it for quoting.
_STATE_WINDOW = 90
_QUOTE_WINDOW = 90

STATE_OPEN = "open"
STATE_RESOLVED = "resolved"
STATE_UNVERIFIED = "unverified"

# Which state wins when one memory names the same ref more than once. Resolved
# beats open for the reason given above; both beat unverified, because a stated
# outcome anywhere in the prose is strictly better evidence than silence
# elsewhere in it. Compared by rank, never by assignment order — a memory that
# says "PR #5 merged" in the title and mentions "PR #5" bare in the body must
# land on resolved regardless of which occurrence the scanner reaches first.
_PRECEDENCE = {STATE_UNVERIFIED: 0, STATE_OPEN: 1, STATE_RESOLVED: 2}


@dataclass(frozen=True)
class Claim:
    """One state assertion a memory makes about one repo-qualified ref."""

    kind: str
    ref: str
    state: str
    evidence: str


def _qualify(text: str, match: re.Match[str], namespace_default: str | None) -> str | None:
    """Repo-qualify a ref, or return None when it cannot be done safely.

    Precedence: an explicit URL naming the same number, then a repo word
    immediately preceding the ref, then the namespace's default repo.
    """
    for url in _URL.finditer(text):
        if url.group(2) == match.group("num"):
            return url.group(1).split("/")[-1]
    named = (match.group("repo") or "").strip().rstrip(":,.").lower()
    if named and namespace_default and named == namespace_default.lower():
        return namespace_default
    if named in _KNOWN_REPO_ALIASES:
        return _KNOWN_REPO_ALIASES[named]
    return namespace_default


# Short forms that appear next to refs and unambiguously name a repo.
_KNOWN_REPO_ALIASES: dict[str, str] = {
    "vtp": "VersatermTechPlatform",
    "versatermtechplatform": "VersatermTechPlatform",
}


def _blank_wikilinks(text: str) -> str:
    """Replace ``[[...]]`` spans with blanks, preserving length and newlines.

    Length is preserved so every *other* ref keeps its offsets: the state
    window and the line-start quote parity both index into this same string,
    and shifting them would silently re-scope unrelated claims. Newlines
    survive for the same reason — ``_is_quoted`` counts delimiter parity from
    the start of the line, so collapsing one would move that boundary.
    """
    return _WIKI.sub(lambda m: "".join(c if c == "\n" else " " for c in m.group(0)), text)


def _is_quoted(text: str, match: re.Match[str]) -> bool:
    """True when the ref sits inside quotes or backticks — cited, not claimed.

    Only ``"`` and a backtick delimit. A bare ``'`` is a possessive or a
    contraction far more often than a quote, and treating it as one makes
    ordinary prose look like a citation.
    """
    # Parity, counted from the start of the LINE - not span-matching over a
    # sliding window. A window that begins mid-string starts on a CLOSING
    # quote, so opening and closing delimiters align on the wrong parity and
    # the scanner ends up matching the `", "` separators BETWEEN quoted items
    # instead of the items themselves. Counting from a known boundary avoids
    # guessing, and a line start is a safe boundary because a quoted span
    # cannot cross a newline.
    line_start = text.rfind("\n", 0, match.start()) + 1
    before = text[line_start : match.start()]
    return any(before.count(delim) % 2 == 1 for delim in ('"', "`"))


def _normalize_kind(raw: str) -> str:
    return "mr" if raw.upper().startswith("M") else "pr"


def extract_claims(
    title: str, content: str, *, namespace_default: str | None = None
) -> list[Claim]:
    """Claims asserted by a memory — at most one per (kind, ref).

    ``title`` is scanned alongside ``content``: a memory titled "PR #174
    MERGED" asserts resolution even when the body narrates the opening.

    A ref whose prose asserts no state is returned as ``STATE_UNVERIFIED``
    rather than dropped — recorded, but never counted as open. See the module
    docs for why the corpus rules out the alternative.

    ``namespace_default`` is the repo a bare ref most likely means in this
    memory's namespace. Pass None for cross-project namespaces so bare refs
    are dropped rather than mis-keyed.

    Refs inside ``[[wiki-links]]`` are ignored — see the module docs.
    """
    text = _blank_wikilinks(f"{title}\n{content}")
    best: dict[tuple[str, str], Claim] = {}
    for match in _REF.finditer(text):
        repo = _qualify(text, match, namespace_default)
        if repo is None:
            continue
        if _is_quoted(text, match):
            continue
        window = text[max(0, match.start() - 20) : match.end() + _STATE_WINDOW]
        if _RESOLVED.search(window):
            state = STATE_RESOLVED
        elif _OPEN.search(window):
            state = STATE_OPEN
        else:
            state = STATE_UNVERIFIED  # named, but the prose says nothing
        kind = _normalize_kind(match.group("kind"))
        key = (kind, f"{repo}#{match.group('num')}")
        existing = best.get(key)
        if existing is not None and _PRECEDENCE[state] <= _PRECEDENCE[existing.state]:
            continue
        best[key] = Claim(
            kind=kind,
            ref=key[1],
            state=state,
            evidence=" ".join(window.split())[:200],
        )
    return list(best.values())
