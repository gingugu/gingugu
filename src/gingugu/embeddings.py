"""Local embedding provider for semantic search.

Wraps fastembed's ONNX-based encoder behind a small protocol so swapping
backends later is a one-file change. Lazy model loading — the model isn't
downloaded or initialized until the first encode call.

The encoder is intentionally PyTorch-free. fastembed ships ONNX runtime
(~50MB) and the default model (BAAI/bge-small-en-v1.5) is ~80MB on disk —
total footprint stays well under sentence-transformers' ~2GB.
"""

from __future__ import annotations

import logging
import struct
from typing import Protocol

logger = logging.getLogger(__name__)

# Default model — strong English retrieval performance, 384-dim, ~80MB.
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"


class EmbeddingProvider(Protocol):
    """Encodes text into a fixed-dim float vector. Returns None on failure.

    Implementations must be safe to share across threads (encoding is the
    only operation we perform).
    """

    model_name: str
    dim: int

    def encode(self, text: str) -> list[float] | None: ...

    def encode_many(self, texts: list[str]) -> list[list[float] | None]: ...

    @property
    def enabled(self) -> bool: ...


class NullEmbeddingProvider:
    """No-op provider — used when embeddings are disabled or fastembed is missing."""

    model_name = "none"
    dim = 0

    def encode(self, text: str) -> list[float] | None:
        return None

    def encode_many(self, texts: list[str]) -> list[list[float] | None]:
        return [None] * len(texts)

    @property
    def enabled(self) -> bool:
        return False


class FastEmbedProvider:
    """fastembed-backed encoder. Lazy-loads the ONNX model on first encode.

    The first encode call may download the model (~80MB) into fastembed's
    local cache (typically ~/.cache/fastembed). All subsequent runs are
    offline.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        self.model_name = model_name
        self.dim = 0
        self._model = None

    @property
    def enabled(self) -> bool:
        return True

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise RuntimeError(
                "fastembed not installed — install gingugu's default deps "
                "or set MEMORY_EMBEDDINGS_ENABLED=false to disable semantic search"
            ) from exc
        logger.info(
            "Loading embedding model %s (first time may download ~80MB)",
            self.model_name,
        )
        self._model = TextEmbedding(model_name=self.model_name)
        sample = next(iter(self._model.embed(["probe"])))
        self.dim = len(sample)
        logger.info("Embedding model ready: dim=%d", self.dim)

    def encode(self, text: str) -> list[float] | None:
        try:
            self._ensure_model()
            vec = next(iter(self._model.embed([text])))
            return list(vec)
        except Exception:
            logger.exception("encode failed; returning None")
            return None

    def encode_many(self, texts: list[str]) -> list[list[float] | None]:
        if not texts:
            return []
        try:
            self._ensure_model()
            return [list(v) for v in self._model.embed(texts)]
        except Exception:
            logger.exception("encode_many failed; returning Nones")
            return [None] * len(texts)


def build_provider(enabled: bool, model_name: str = DEFAULT_MODEL) -> EmbeddingProvider:
    """Factory: returns FastEmbedProvider if enabled and importable, else Null.

    Probes the fastembed import lazily so a disabled provider never touches it.
    """
    if not enabled:
        return NullEmbeddingProvider()
    try:
        import fastembed  # noqa: F401
    except ImportError:
        logger.warning(
            "fastembed not installed; semantic search disabled. "
            "Install with default deps or set MEMORY_EMBEDDINGS_ENABLED=false to silence."
        )
        return NullEmbeddingProvider()
    return FastEmbedProvider(model_name=model_name)


# --- Binary serialization for SQLite BLOB storage --------------------------
#
# Float32 little-endian packed array. 384 dims × 4 bytes = 1.5KB per memory.
# Decode is a single struct.unpack call — no numpy dependency required at
# the storage layer (fastembed brings numpy in for encoding, but readers
# stay stdlib-only).


def pack(vec: list[float]) -> bytes:
    """Serialize a float vector to little-endian float32 bytes."""
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack(blob: bytes) -> list[float]:
    """Deserialize float32 bytes back into a Python list of floats."""
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors. Returns 0.0 on degenerate input."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=False):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / ((na**0.5) * (nb**0.5))
