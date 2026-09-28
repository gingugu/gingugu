"""The text a memory is embedded from: one recipe, shared by every caller.

Kept apart from the providers in ``embeddings`` because it is not provider code.
It is a contract between the write path that stores a memory's vector and
pieces, and every read path that re-derives the same text - piece spans are
character offsets into it, so two callers that disagree slice the wrong words.
"""

from __future__ import annotations


def embedding_input(title: str, content: str, about: str | None = None) -> str:
    """The text fed into the embedder for a memory.

    Title carries strong signal so we prepend it - fastembed's BGE models
    handle short prefixes well. ``about`` follows it, ahead of the content, so
    a long body can never push it past the encoder's window. Without ``about``
    the text is byte-identical to what every stored vector was built from, so
    adding the field left nothing stale. Piece spans (``chunking``) are
    offsets into this text, so every reader must pass the same ``about``.

    This lives here, not in the storage layer, because two callers must agree
    on it exactly: the write path that persists a memory's vector, and any
    read path that encodes a fresh payload to compare against those vectors.
    A drift between the two recipes would not raise; it would quietly compare
    vectors built from different text and return a plausible wrong number.
    """
    if about:
        return f"{title}\n\n{about}\n\n{content}"
    return f"{title}\n\n{content}"
