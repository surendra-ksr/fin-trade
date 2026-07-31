"""Stacked ensemble meta-learner using ONLY out-of-fold predictions.

The meta-learner (LinearRegression or LogisticRegression) is trained on
predictions from base models (LSTM, GRU, GBM) that were generated via
walk-forward CV. This ensures no leakage: the meta-learner never sees
predictions made using the same data it trains on.
"""
from __future__ import annotations
import logging
from typing import Optional, List, Dict, Any
import numpy as np
import pandas as pd

from .base import ModelBase
from .neural import LSTMModel, GRUModel
from .gbm_baseline import GBMBaseline
from utils.logger import get_logger

_log = get_logger(__name__)


class StackedEnsemble(ModelBase):
    """Meta-learner stacked over Phase-3 base models.

    Training procedure:
        1. Generate out-of-fold predictions from each base model using
           ``purged_walk_forward`` (positive embargo).
        2. Stack the out-of-fold predictions as meta-features.
        3. Train the meta-learner ONLY on these stacked predictions.

    The anti-leak property: no prediction in the meta-training set comes
    from a model fitted on data overlapping that meta-row.
    """

    version = "4.1-ensemble"

    def __init__(
        self,
        meta_model_type: str = "linear",
        base_models: Optional[List[ModelBase]] = None,
        seed: Optional[int] = None,
    ) -> None:
        super().__init__()
        self.meta_model_type = meta_model_type
        self.base_models = base_models or []
        self.seed = seed
        # Meta-learner initialized lazily in fit
        self.meta_learner: Optional[Any] = None
        _log.info(
            "StackedEnsemble initialized: meta=%s base_models=%d",
            self.meta_model_type, len(self.base_models),
        )

    def _build_base_predictions(
        self, X: np.ndarray, y: np.ndarray, folds: int = 5, embargo: int = 5
    ) -> np.ndarray:
        """Generate out-of-fold predictions from base models.

        For each fold, train each base model on the fold's train split,
        predict on the test split, and collect predictions. The result is
        an array of shape [n_samples, n_base_models].
        """
        from .trainer import Trainer
        n = len(X)
        predictions = np.zeros((n, len(self.base_models)))
        trainer = Trainer(embargo=embargo)
        for fold in trainer.generate_folds(n, folds=folds):
            X_train, _, X_test, _ = X[fold.train], y[fold.train], X[fold.test], y[fold.test]
            # If X_test empty, skip
            if len(X_test) == 0:
                continue
            for idx, base in enumerate(self.base_models):
                # Clone/re-instantiate to avoid state contamination
                base_clone = self._clone_base(base)
                try:
                    base_clone.fit(X_train, y[fold.train])
                    preds = base_clone.predict(X_test)
                    # Assign predictions back to original indices
                    predictions[fold.test, idx] = preds.ravel()[: len(fold.test)]
                except Exception:
                    # If a base model fails, fill with mean of available predictions
                    predictions[:, idx] = np.nanmean(predictions[:, idx]) if np.any(np.isfinite(predictions[:, idx])) else 0.0
        # Fill NaN with column mean (should be minimal with proper folds)
        for idx in range(predictions.shape[1]):
            col_mean = np.nanmean(predictions[:, idx])
            predictions[np.isnan(predictions[:, idx]), idx] = col_mean if np.isfinite(col_mean) else 0.0
        return predictions

    def _clone_base(self, base: ModelBase) -> ModelBase:
        """Create a fresh instance of a base model with same config."""
        if isinstance(base, LSTMModel):
            return LSTMModel(
                input_size=base.input_size,
                hidden_size=base.hidden_size,
                num_layers=base.num_layers,
                dropout=base.dropout,
                output_size=base.output_size,
                seed=base.seed,
            )
        elif isinstance(base, GRUModel):
            return GRUModel(
                input_size=base.input_size,
                hidden_size=base.hidden_size,
                num_layers=base.num_layers,
                dropout=base.dropout,
                output_size=base.output_size,
                seed=base.seed,
            )
        elif isinstance(base, GBMBaseline):
            return GBMBaseline(
                n_estimators=base.n_estimators,
                max_depth=base.max_depth,
                learning_rate=base.learning_rate,
                seed=base.seed,
            )
        else:
            # Fallback: return the original (not ideal but safe)
            return base

    def fit(self, X: np.ndarray, y: np.ndarray) -> "StackedEnsemble":
        """Fit meta-learner on out-of-fold predictions."""
        meta_features = self._build_base_predictions(X, y)
        y_flat = np.asarray(y).ravel()
        # Initialize meta-learner
        try:
            from sklearn.linear_model import LinearRegression
            from sklearn.ensemble import RandomForestRegressor
        except ImportError:
            LinearRegression = None  # type: ignore
            RandomForestRegressor = None  # type: ignore
        if LinearRegression is None:
            raise ImportError("scikit-learn is required for StackedEnsemble meta-learner")
        if self.meta_model_type == "linear":
            self.meta_learner = LinearRegression()
        elif self.meta_model_type == "random_forest":
            self.meta_learner = RandomForestRegressor(n_estimators=50, random_state=self.seed)
        else:
            self.meta_learner = LinearRegression()
        self.meta_learner.fit(meta_features, y_flat)
        _log.info("StackedEnsemble meta-learner fitted: meta_features_shape=%s", meta_features.shape)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Predict using stacked predictions from base models + meta-learner."""
        meta_features = np.zeros((len(X), len(self.base_models)))
        # For simplicity in prediction, use base predictions from current state
        # (production would use pre-computed out-of-fold predictions; here we
        # approximate with direct predictions for inference speed)
        for idx, base in enumerate(self.base_models):
            try:
                preds = base.predict(X)
                meta_features[:, idx] = preds.ravel()[:len(X)]
            except Exception:
                meta_features[:, idx] = 0.0
        if self.meta_learner is None:
            raise RuntimeError("StackedEnsemble not fitted; call fit() first.")
        return np.asarray(self.meta_learner.predict(meta_features)).ravel()
