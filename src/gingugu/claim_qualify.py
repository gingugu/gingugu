"""Repo-qualification for state-claim refs.

Split from ``claims`` to keep both modules under the 300-line limit: this is
the separable question of WHICH REPO a ref names, distinct from what state the
prose asserts about it.

Every rule here is pinned to a real misfire measured over the live corpus.
"""

from __future__ import annotations

import re

# Explicit URL wins over everything - it names the repo unambiguously.
_URL = re.compile(
    r"https?://(?:www\.)?(?:github|gitlab)\.com/([\w.-]+(?:/[\w.-]+)*?)/"
    r"(?:pull|-/merge_requests|merge_requests)/(\d+)",
    re.I,
)

# The sigil is MANDATORY, and only spaces/tabs may separate it from the kind.
#
# Both restrictions are measured, not stylistic. Optional sigil: bare ``PR 3``
# in prose names a POSITION in a planned series, never an identity - "PR 0 -
# Python CI workflow", "do not go looking for a PR 5; there isn't one". One
# memory settles it in a single sentence: "(#193 PR0, #194 PR1, #196 PR2)" -
# sigil on the real ref, absent on the ordinal. An existence check cannot save
# this, because PRs #1-#5 genuinely exist in the repos concerned.
#
# ``\s*`` instead of ``[ \t]*``: whitespace spanned a line break, so a list
# item ending "NO PR" bound to the "2." opening the NEXT item and produced a
# claim asserting the exact opposite of the prose. A ref never crosses a line.
_REF = re.compile(
    r"(?:(?<![\w./-])(?P<repo>[A-Za-z][\w.-]{2,30})[ \t]+)?"
    r"\b(?P<kind>PR|MR|pull request|merge request)[ \t]*[#!](?P<num>\d+)"
    r"(?:[ \t]*\((?P<repo_after>[A-Za-z][\w.-]{2,30})\)"
    r"|[ \t]+(?P<repo_word>[A-Za-z][\w.-]{2,30})\b)?"
)

# A token that precedes a ref is a REPO NAME rather than an ordinary English
# word when it carries punctuation a word would not (``api-gateway``,
# ``gingugu.com``) or an internal capital (``platform-infra``). Kept
# deliberately narrow: "Fixed in PR #873" must read "in" as prose, not a repo.
#
# The lookbehind on the repo group is what keeps this honest. Without it,
# "branch feature/serve-transport. PR #10" reads the tail of a PATH as a repo
# name - it is hyphenated, so it passes every shape test - and keys the claim
# to ``serve-transport``. A repo name is never preceded by a path separator.
#
# Narrow on purpose, and narrowed AGAIN after measurement. A single hyphen was
# the first attempt; replayed over the live corpus it invented three repos out
# of ordinary prose - ``PROJ-8#65`` from a Jira ticket, ``docs-only#168`` and
# ``re-point#155`` from hyphenated English. Hyphens are common in words and in
# ticket ids; two hyphens, an internal capital, or a domain suffix are not.
#
# Failing this test is cheap: the ref falls back to the namespace default,
# exactly as it did before. Passing it wrongly invents a repo. When in doubt
# this must say no - and every repo the store actually knows arrives through
# ``known_repos`` without consulting this at all.
_REPO_SHAPED = re.compile(r"[a-z][A-Z]|-[\w.]*-|\.(?:com|io|dev|org|net|ai)$")

# A ticket id (PROJ-8, PROJ-85) is not a repo, and it sits next to refs
# constantly in this corpus. Checked before shape, since some ids would
# otherwise pass on a second hyphen.
_TICKET = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")


def _named_repo(
    match: re.Match[str], namespace_default: str | None, known_repos: frozenset[str]
) -> str | None:
    """The repo this ref NAMES outright, or None when the prose names none.

    An explicitly named repo is authoritative even when it is one nothing here
    recognizes. The old code computed this token, found it on neither the
    default nor a two-entry alias list, then discarded it and substituted the
    namespace default - so "documented in VendorOS PR #115" was recorded
    against ``api-gateway``, and no ``VendorOS`` claim existed at all.
    Learning the repo and then guessing anyway is worse than not looking.
    """
    for group in ("repo", "repo_after", "repo_word"):
        resolved = _resolve_token(match.group(group), namespace_default, known_repos)
        if resolved is not None:
            return resolved
    return None


def _resolve_token(
    token: str | None, namespace_default: str | None, known_repos: frozenset[str]
) -> str | None:
    """One candidate token, resolved to a repo name or rejected."""
    raw = (token or "").strip().rstrip(":,.")
    if not raw:
        return None
    lowered = raw.lower()
    if namespace_default and lowered == namespace_default.lower():
        return namespace_default
    if lowered in _KNOWN_REPO_ALIASES:
        return _KNOWN_REPO_ALIASES[lowered]
    for repo in known_repos:
        if repo.lower() == lowered:
            return repo
    if _TICKET.match(raw):
        return None
    return raw if _REPO_SHAPED.search(raw) else None


def _document_bindings(
    text: str, namespace_default: str | None, known_repos: frozenset[str]
) -> dict[str, str]:
    """Every number->repo binding this document states outright.

    A memory that qualifies "platform-infra PR #873" once, then writes
    "Fixed in PR #873" two sentences later, is talking about one PR both times.
    Qualifying each occurrence in isolation emitted two refs for it, the second
    against a repo where it does not exist. Bindings are what the document
    already told us; consulting them costs one extra pass.

    Keyed by number alone, so the first binding wins when one memory names the
    same number in two repos. That is rare, and still strictly better than
    defaulting every bare mention to the namespace.
    """
    bindings: dict[str, str] = {}
    for url in _URL.finditer(text):
        bindings.setdefault(url.group(2), url.group(1).split("/")[-1])
    for match in _REF.finditer(text):
        repo = _named_repo(match, namespace_default, known_repos)
        if repo is not None:
            bindings.setdefault(match.group("num"), repo)
    return bindings


def _qualify(
    text: str,
    match: re.Match[str],
    namespace_default: str | None,
    known_repos: frozenset[str] = frozenset(),
    bindings: dict[str, str] | None = None,
) -> str | None:
    """Repo-qualify a ref, or return None when it cannot be done safely.

    Precedence: an explicit URL naming the same number, then a repo the prose
    names next to the ref, then a binding stated elsewhere in the same
    document, then the namespace's default repo.
    """
    for url in _URL.finditer(text):
        if url.group(2) == match.group("num"):
            return url.group(1).split("/")[-1]
    named = _named_repo(match, namespace_default, known_repos)
    if named is not None:
        return named
    if bindings is not None:
        bound = bindings.get(match.group("num"))
        if bound is not None:
            return bound
    return namespace_default


# Short forms that appear next to refs and unambiguously name a repo.
_KNOWN_REPO_ALIASES: dict[str, str] = {
    "pinf": "platform-infra",
    "platform-infra": "platform-infra",
}
