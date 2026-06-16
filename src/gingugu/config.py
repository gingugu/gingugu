"""Configuration loader and logging setup.

All config comes from environment variables (see README). Logging goes to
**stderr only** — stdout is reserved for the MCP stdio transport.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import platformdirs


def _default_db_path(platform: str = sys.platform) -> Path:
    """OS-appropriate default DB location.

    Windows gets the idiomatic ``%LOCALAPPDATA%\\gingugu`` via platformdirs.
    macOS and Linux deliberately keep ``~/.local/share/gingugu`` (XDG-style):
    it matches every existing install — platformdirs would move macOS to
    ~/Library/Application Support and strand existing databases.
    """
    if platform == "win32":
        return Path(platformdirs.user_data_dir("gingugu", appauthor=False)) / "memories.db"
    return Path.home() / ".local" / "share" / "gingugu" / "memories.db"


_DEFAULT_DB = _default_db_path()

# Composite-score weight defaults (see docs/architecture.md → Scoring).
# Confidence (trust) is the dominant standalone signal; freshness is a gentle
# recency tiebreaker, not an eraser — dormant memories must stay retrievable.
_DEFAULT_WEIGHTS = {
    "relevance": 0.45,
    "freshness": 0.10,
    "access": 0.10,
    "confidence": 0.35,
}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        logging.getLogger(__name__).warning(
            "Invalid float for %s=%r; using default %s", name, raw, default
        )
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logging.getLogger(__name__).warning(
            "Invalid int for %s=%r; using default %s", name, raw, default
        )
        return default


@dataclass(frozen=True)
class Config:
    """Resolved runtime configuration."""

    db_path: Path
    namespace: str | None
    namespace_path: str | None
    auto_context_limit: int
    decay_lambda: float
    embeddings_enabled: bool = True
    embeddings_model: str = "BAAI/bge-small-en-v1.5"
    embeddings_backend: str = "fastembed"
    embeddings_ollama_host: str = "http://localhost:11434"
    embeddings_ollama_model: str = "nomic-embed-text"
    weights: dict[str, float] = field(default_factory=lambda: dict(_DEFAULT_WEIGHTS))
    log_level: str = "INFO"

    @property
    def resolved_namespace(self) -> str | None:
        """Namespace from MEMORY_NAMESPACE, else basename of MEMORY_NAMESPACE_PATH."""
        if self.namespace:
            return self.namespace
        if self.namespace_path:
            base = Path(self.namespace_path).name
            return base or None
        return None


def _load_weights() -> dict[str, float]:
    """Load and **normalize** scoring weights so they always sum to 1.0.

    Users may override any subset via MEMORY_W_*; ratios are what matter. If
    the sum is zero (all zeroed), fall back to defaults with a warning.
    """
    raw = {
        "relevance": _env_float("MEMORY_W_RELEVANCE", _DEFAULT_WEIGHTS["relevance"]),
        "freshness": _env_float("MEMORY_W_FRESHNESS", _DEFAULT_WEIGHTS["freshness"]),
        "access": _env_float("MEMORY_W_ACCESS", _DEFAULT_WEIGHTS["access"]),
        "confidence": _env_float("MEMORY_W_CONFIDENCE", _DEFAULT_WEIGHTS["confidence"]),
    }
    # Clamp negatives to 0 — negative weights are nonsensical.
    raw = {k: max(0.0, v) for k, v in raw.items()}
    total = sum(raw.values())
    if total <= 0:
        logging.getLogger(__name__).warning(
            "All scoring weights are zero/invalid; falling back to defaults."
        )
        return dict(_DEFAULT_WEIGHTS)
    return {k: v / total for k, v in raw.items()}


def load_config() -> Config:
    """Build a Config from the current environment."""
    raw_db_path = os.environ.get("MEMORY_DB_PATH")
    if raw_db_path:
        db_path = Path(raw_db_path).expanduser()
    else:
        db_path = _DEFAULT_DB
    # MEMORY_DEBUG is a convenience switch for DEBUG logging; explicit
    # MEMORY_LOG_LEVEL still wins if set.
    default_level = "DEBUG" if _env_bool("MEMORY_DEBUG") else "INFO"
    return Config(
        db_path=db_path,
        namespace=os.environ.get("MEMORY_NAMESPACE") or None,
        namespace_path=os.environ.get("MEMORY_NAMESPACE_PATH") or None,
        auto_context_limit=_env_int("MEMORY_AUTO_CONTEXT_LIMIT", 10),
        decay_lambda=_env_float("MEMORY_DECAY_LAMBDA", 0.01),
        embeddings_enabled=_env_bool("MEMORY_EMBEDDINGS_ENABLED", default=True),
        embeddings_model=os.environ.get("MEMORY_EMBEDDINGS_MODEL") or "BAAI/bge-small-en-v1.5",
        embeddings_backend=os.environ.get("MEMORY_EMBEDDINGS_BACKEND") or "fastembed",
        embeddings_ollama_host=(
            os.environ.get("MEMORY_EMBEDDINGS_OLLAMA_HOST") or "http://localhost:11434"
        ),
        embeddings_ollama_model=(
            os.environ.get("MEMORY_EMBEDDINGS_OLLAMA_MODEL") or "nomic-embed-text"
        ),
        weights=_load_weights(),
        log_level=os.environ.get("MEMORY_LOG_LEVEL", default_level).upper(),
    )


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging to stderr (never stdout — that's the MCP transport)."""
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
