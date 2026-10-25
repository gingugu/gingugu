"""The Explorer API's environment and error type.

``UiEnv`` is everything a handler needs that is not in the request: which DB
file, how to get an embedder, and the scoring knobs. The embedder is a factory,
called lazily, because loading a model is slow and only ``/search`` needs one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..config import load_config
from ..embeddings import EmbeddingProvider


class ApiError(Exception):
    """A request the API refuses; carries the HTTP status to answer with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class UiEnv:
    db_path: Path
    embedder: Callable[[], EmbeddingProvider | None]
    weights: dict[str, float] | None = None
    decay_lambda: float | None = None
    _embedder_cache: list = field(default_factory=list, repr=False)

    def get_embedder(self) -> EmbeddingProvider | None:
        """Build the embedder on first use and keep it."""
        if not self._embedder_cache:
            self._embedder_cache.append(self.embedder())
        return self._embedder_cache[0]

    def get_weights(self) -> dict[str, float]:
        return self.weights if self.weights is not None else load_config().weights

    def get_decay_lambda(self) -> float:
        if self.decay_lambda is not None:
            return self.decay_lambda
        return load_config().decay_lambda
