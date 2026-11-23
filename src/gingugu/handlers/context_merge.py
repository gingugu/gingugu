"""Merging per-namespace ``memory_context`` loads into one ordered list.

Split out of ``recall`` so the handler module reads as the two tools and
nothing else; the merge is a pure function over already-loaded memories.
"""

from __future__ import annotations

from ..models import Memory


def merge_namespace_context(
    pins: list[Memory],
    tails: list[list[Memory]],
    best: dict[str, Memory],
) -> list[Memory]:
    """Merge per-namespace context loads without destroying their order.

    ``build_context`` has already ordered each namespace: pins, then a
    quota-selected ranked tail. This preserves both, because composite scores
    are not comparable *across* namespaces - corpora differ in size, access
    volume and age, so the same number means something different in each - and
    they are not comparable across buckets *within* one either, since only the
    task bucket carries a real search relevance. Sorting the merged set by that
    number ranks memories against each other on a scale none of them share.

    So: every namespace's pins first, then the ranked tails interleaved by rank
    position. Interleaving is what keeps a multi-namespace load honest - plain
    concatenation would bury the second namespace's freshest memory beneath the
    first namespace's entire list.
    """
    out: list[Memory] = []
    seen: set[str] = set()

    def emit(mem: Memory, *, authoritative: bool = False) -> None:
        # A memory surfacing in two namespaces is emitted once, at its earliest
        # position, using whichever instance won de-duplication.
        #
        # A pin is emitted as ITSELF. De-dup keeps the highest-scoring instance
        # and a pin scores None, so a scored duplicate from another namespace's
        # cross-namespace bucket wins - and scorelessness is precisely how a
        # caller knows the memory bypassed ranking.
        if mem.id not in seen:
            seen.add(mem.id)
            out.append(mem if authoritative else best.get(mem.id, mem))

    for mem in pins:
        emit(mem, authoritative=True)
    for rank in range(max((len(t) for t in tails), default=0)):
        for tail in tails:
            if rank < len(tail):
                emit(tail[rank])
    return drop_promoted_copies(out)


def drop_promoted_copies(memories: list[Memory]) -> list[Memory]:
    """Drop a promoted copy whose source is in the same load.

    A copy carries a new id, so the id de-dup above cannot see it: an owner
    loading ``crow`` and ``crew`` would read the same lesson twice. The source
    wins - it is the one that gets edited. A copy whose source is not loaded
    (every mate's view of the commons) stays.
    """
    from ..promotion import source_of

    ids = {m.id for m in memories}
    return [m for m in memories if not ((src := source_of(m.metadata)) and src["id"] in ids)]
