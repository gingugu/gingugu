"""Embedding providers for semantic search.

Supports two backends, selected via MEMORY_EMBEDDINGS_BACKEND:

- **fastembed** (default) — ONNX-based local encoder. Lazy-loads the model
  on first use (~80MB into ~/.cache/fastembed). PyTorch-free.
- **ollama** — delegates to a running Ollama process via its HTTP API.
  Zero extra memory footprint — uses whatever embedding model Ollama already
  has loaded. Requires Ollama to be running (``ollama serve``).

Both implement the ``EmbeddingProvider`` protocol so storage and search are
backend-agnostic.
"""

from __future__ import annotations

import json
import logging
import struct
import urllib.request
from typing import Protocol

logger = logging.getLogger(__name__)

# Default model — strong English retrieval performance, 384-dim, ~80MB.
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "nomic-embed-text"


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


class OllamaEmbeddingProvider:
    """Ollama-backed encoder via the local Ollama HTTP API.

    Calls ``POST /api/embeddings`` on the running Ollama process instead of
    loading an ONNX model into this process. Zero extra memory footprint —
    Ollama is assumed to already be running with an embedding model loaded.

    Dimensions are detected automatically on the first successful encode call.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_OLLAMA_MODEL,
        host: str = DEFAULT_OLLAMA_HOST,
    ) -> None:
        self.model_name = model_name
        self._host = host.rstrip("/")
        self.dim = 0

    @property
    def enabled(self) -> bool:
        return True

    def _call(self, text: str) -> list[float] | None:
        url = f"{self._host}/api/embeddings"
        payload = json.dumps({"model": self.model_name, "prompt": text}).encode()
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read())
            vec: list[float] = body["embedding"]
            if self.dim == 0:
                self.dim = len(vec)
            return vec
        except Exception:
            logger.exception(
                "Ollama embed call failed (host=%s model=%s)", self._host, self.model_name
            )
            return None

    def encode(self, text: str) -> list[float] | None:
        return self._call(text)

    def encode_many(self, texts: list[str]) -> list[list[float] | None]:
        return [self._call(t) for t in texts]


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


def build_provider(
    enabled: bool,
    model_name: str = DEFAULT_MODEL,
    backend: str = "fastembed",
    ollama_host: str = DEFAULT_OLLAMA_HOST,
    ollama_model: str = DEFAULT_OLLAMA_MODEL,
) -> EmbeddingProvider:
    """Factory: returns the configured EmbeddingProvider or Null.

    Backend is selected by ``backend``:
    - ``"fastembed"`` (default) — ONNX local encoder; falls back to Null if
      fastembed is not installed.
    - ``"ollama"`` — delegates to the local Ollama HTTP API; falls back to
      Null if Ollama is not reachable at ``ollama_host``.
    """
    if not enabled:
        return NullEmbeddingProvider()

    if backend == "ollama":
        provider = OllamaEmbeddingProvider(model_name=ollama_model, host=ollama_host)
        test = provider.encode("probe")
        if test is None:
            logger.warning(
                "Ollama not reachable at %s; semantic search disabled. "
                "Ensure Ollama is running or set MEMORY_EMBEDDINGS_BACKEND=fastembed.",
                ollama_host,
            )
            return NullEmbeddingProvider()
        logger.info("Ollama embedding backend ready: model=%s dim=%d", ollama_model, provider.dim)
        return provider

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
