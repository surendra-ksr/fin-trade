"""Versioned model contracts, DB registry, and leakage-safe validation primitives."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
import pickle
import sqlite3
import json
from pathlib import Path
from typing import Any, Optional, Mapping, Generator
import numpy as np
import pandas as pd

from data.database import get_database
from utils.logger import get_logger

_log = get_logger("models.base")

# ------------------------------------------------------------------
# Registry table initialization (idempotent; called on import)
# ------------------------------------------------------------------
_REGISTRY_INIT_SQL = """
CREATE TABLE IF NOT EXISTS model_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_name TEXT NOT NULL,
    version TEXT NOT NULL,
    file_path TEXT,
    metrics_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (model_name, version)
);
CREATE INDEX IF NOT EXISTS idx_model_registry_name ON model_registry(model_name);
"""

try:
    _db_init = get_database()
    with _db_init.transaction() as tx:
        tx.execute("CREATE TABLE IF NOT EXISTS model_registry (id INTEGER PRIMARY KEY AUTOINCREMENT, model_name TEXT NOT NULL, version TEXT NOT NULL, file_path TEXT, metrics_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (model_name, version))")
        tx.execute("CREATE INDEX IF NOT EXISTS idx_model_registry_name ON model_registry(model_name)")
except Exception as exc:
    _log.warning("model registry DB init deferred: {}", exc)


class ModelBase(ABC):
    """Common serializable model contract with versioned file persistence.

    Subclasses must implement ``fit`` (training only, no mutation of inputs),
    ``predict`` (inference only), and expose ``version``.
    """

    version: str = "1.0"

    @abstractmethod
    def fit(self, X: np.ndarray | pd.DataFrame, y: np.ndarray) -> "ModelBase":
        """Fit using training data only; return self for chaining."""
        ...

    @abstractmethod
    def predict(self, X: np.ndarray | pd.DataFrame) -> np.ndarray:
        """Predict without mutating inputs; return numeric array."""
        ...

    def save(self, path: str | Path) -> None:
        """Persist model and version metadata to a versioned pickle file."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": self.version, "model": self}
        Path(path).write_bytes(pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL))
        _log.info("model saved to {} (version={})", path, self.version)

    @classmethod
    def load(cls, path: str | Path) -> "ModelBase":
        """Load a serialized versioned model from file."""
        payload = pickle.loads(Path(path).read_bytes())
        model: ModelBase = payload["model"]
        if not isinstance(model, ModelBase):
            raise TypeError(f"loaded object is not a ModelBase: {type(model)}")
        _log.info("model loaded from {} (version={})", path, payload.get("version"))
        return model

    # ------------------------------------------------------------------
    # Versioned DB registry
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        file_path: Optional[str | Path] = None,
        metrics: Optional[Mapping[str, Any]] = None,
    ) -> int:
        """Persist model metadata to the SQLite registry; return registry id."""
        db = get_database()
        now = pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        cursor = db.execute(
            "INSERT OR IGNORE INTO model_registry (model_name, version, file_path, metrics_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (name, self.version, str(file_path) if file_path else None,
             json.dumps(metrics) if metrics else None, now, now),
        )
        row_id = cursor.lastrowid
        # If the insert was ignored (duplicate key), fetch existing id
        if row_id is None or row_id == 0:
            row = db.query_one(
                "SELECT id FROM model_registry WHERE model_name = ? AND version = ?",
                (name, self.version),
            )
            row_id = int(row["id"]) if row else None
        _log.info("model registered: {} v{} id={}", name, self.version, row_id)
        return int(row_id) if row_id is not None else 0

    @classmethod
    def registry_list(cls, name: Optional[str] = None) -> list[dict[str, Any]]:
        """List registered model entries; filter by *name* when given."""
        db = get_database()
        if name is not None:
            rows = db.query(
                "SELECT * FROM model_registry WHERE model_name = ? ORDER BY updated_at DESC",
                (name,),
            )
        else:
            rows = db.query("SELECT * FROM model_registry ORDER BY updated_at DESC")
        return rows

    @classmethod
    def registry_load(cls, name: str, version: Optional[str] = None) -> Optional["ModelBase"]:
        """Load a registered model by name (optional version); returns None if missing."""
        db = get_database()
        if version is not None:
            row = db.query_one(
                "SELECT file_path FROM model_registry WHERE model_name = ? AND version = ? ORDER BY updated_at DESC LIMIT 1",
                (name, version),
            )
        else:
            row = db.query_one(
                "SELECT file_path FROM model_registry WHERE model_name = ? ORDER BY updated_at DESC LIMIT 1",
                (name,),
            )
        if row is None or not row.get("file_path"):
            return None
        return cls.load(Path(row["file_path"]))


# ------------------------------------------------------------------
# Purged CV and sequence helpers
# ------------------------------------------------------------------

@dataclass(frozen=True)
class Fold:
    """Purged train/test index arrays with a non-zero embargo gap."""
    train: np.ndarray
    test: np.ndarray
    embargo: int


def purged_walk_forward(
    n: int, folds: int = 5, embargo: int = 1
) -> Generator[Fold, None, None]:
    """Yield expanding walk-forward folds with embargo rows removed before test.

    Args:
        n: total number of observations.
        folds: number of folds (must be >= 2).
        embargo: number of rows removed between train end and test start (> 0).
    Returns:
        Generator of ``Fold`` objects with ``train``, ``test``, and ``embargo``.
    Raises:
        ValueError: if ``folds < 2`` or ``embargo < 1``.
    """
    import typing
    if folds < 2:
        raise ValueError("folds must be >= 2")
    if embargo < 1:
        raise ValueError("embargo must be > 0")
    edges = np.linspace(0, n, folds + 1, dtype=int)
    for i in range(1, folds):
        test_start = edges[i]
        test_end = edges[i + 1]
        train_end = max(0, test_start - embargo)
        train = np.arange(0, train_end)
        test = np.arange(test_start, test_end)
        yield Fold(train, test, embargo)


def past_sequences(values: np.ndarray, window: int) -> np.ndarray:
    """Build causal sequences: each row i uses only ``values[i-window:i]``.

    The target associated with sequence at index ``i`` must follow after
    ``values[i-window:i]``, ensuring no future leakage.
    """
    x = np.asarray(values)
    if window < 1:
        raise ValueError("window must be >= 1")
    if len(x) < window:
        return np.empty((0, window))
    return np.stack([x[i - window : i] for i in range(window, len(x))])
