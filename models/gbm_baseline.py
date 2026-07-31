"""Gradient Boosting Machine baseline using scikit-learn.

Provides fit, predict, save, load with a simple interface consistent with
``models.base.ModelBase``.
"""
from __future__ import annotations
import logging
from pathlib import Path
from typing import Any, Optional, Mapping
import pickle

import numpy as np

from .base import ModelBase

_log = logging.getLogger(__name__)

try:
    from sklearn.ensemble import GradientBoostingRegressor
    _SKLEARN_AVAILABLE = True
except ImportError:  # pragma: no cover
    _SKLEARN_AVAILABLE = False


class GBMBaseline(ModelBase):
    """Gradient Boosting Machine baseline for regression.

    Args:
        n_estimators: number of boosting stages.
        max_depth: maximum depth of individual trees.
        learning_rate: shrinkage factor.
        seed: optional reproducibility seed.
    """

    version = "3.1-gbm"

    def __init__(
        self,
        n_estimators: int = 100,
        max_depth: int = 3,
        learning_rate: float = 0.1,
        seed: Optional[int] = None,
    ) -> None:
        super().__init__()
        if not _SKLEARN_AVAILABLE:
            raise ImportError("scikit-learn is required for GBMBaseline")
        self.n_estimators = int(n_estimators)
        self.max_depth = int(max_depth)
        self.learning_rate = float(learning_rate)
        self.seed = seed
        self.model = GradientBoostingRegressor(
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            learning_rate=self.learning_rate,
            random_state=self.seed,
        )
        _log.info(
            "GBM baseline initialized: estimators=%d depth=%d lr=%.2f",
            self.n_estimators, self.max_depth, self.learning_rate,
        )

    def fit(self, X: np.ndarray, y: np.ndarray) -> "GBMBaseline":
        """Fit the GBM; X must be 2-D [samples, features]."""
        X_np = np.asarray(X)
        y_np = np.asarray(y).ravel()
        if X_np.ndim != 2:
            raise ValueError(f"GBMBaseline expects 2-D X, got shape {X_np.shape}")
        self.model.fit(X_np, y_np)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Predict; return 1-D array of predictions."""
        X_np = np.asarray(X)
        if X_np.ndim == 1:
            X_np = X_np.reshape(1, -1)
        return self.model.predict(X_np)
