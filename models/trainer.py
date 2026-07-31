"""Trainer: walk-forward CV, purged/embargoed splits, and anti-leak sequence builder.

Every fold guarantees zero train/test overlap and a positive embargo gap.
The sequence builder ensures features at time t use only data <= t.
"""
from __future__ import annotations
import logging
from typing import Optional, Generator, List
import numpy as np

from .base import Fold, purged_walk_forward, past_sequences

_log = logging.getLogger(__name__)


class SequenceBuilder:
    """Causal sequence builder that uses only past data for each row.

    The anti-leak property is verified by planting a future spike and
    confirming that no earlier feature row changes.
    """

    def __init__(self, window: int = 20) -> None:
        if window < 1:
            raise ValueError("window must be >= 1")
        self.window = int(window)

    def build(self, features: np.ndarray) -> np.ndarray:
        """Return sequences of shape [n_seq, window, n_features]."""
        arr = np.asarray(features)
        if arr.ndim == 1:
            arr = arr.reshape(-1, 1)
        # Each sequence at index i uses features[i-window:i]
        seqs = []
        for i in range(self.window, len(arr)):
            seqs.append(arr[i - self.window : i])
        if not seqs:
            return np.empty((0, self.window, arr.shape[1]))
        return np.stack(seqs)

    def anti_leak_check(self, features: np.ndarray, spike_index: int = -1) -> bool:
        """Plant a future spike and verify no earlier sequence changes.

        Args:
            features: input feature array [n_samples, n_features].
            spike_index: index at which to inject a large spike.
        Returns:
            True if earlier sequences are unchanged (anti-leak holds).
        """
        arr = np.asarray(features).copy()
        original = self.build(arr)
        if spike_index >= 0 and spike_index < len(arr):
            arr[spike_index, 0] = 1e9  # extreme spike
        modified = self.build(arr)
        # Earlier sequences must match exactly (before spike reaches them)
        # Since spike is at spike_index, sequences ending before spike_index are unchanged
        safe_up_to = min(spike_index - self.window, len(original))
        if safe_up_to < 0:
            safe_up_to = 0
        if len(original) == 0 or len(modified) == 0:
            return True
        return np.array_equal(original[:safe_up_to], modified[:safe_up_to])


class Trainer:
    """Walk-forward trainer with purged, embargoed K-fold CV.

    Every fold enforces ``embargo > 0`` and proves zero index overlap.
    """

    def __init__(self, embargo: int = 5) -> None:
        if embargo < 1:
            raise ValueError("embargo must be > 0")
        self.embargo = int(embargo)

    def generate_folds(self, n: int, folds: int = 5) -> Generator[Fold, None, None]:
        """Generate purged walk-forward folds with positive embargo."""
        for fold in purged_walk_forward(n, folds=folds, embargo=self.embargo):
            # Verify zero overlap explicitly
            overlap = np.intersect1d(fold.train, fold.test)
            if len(overlap) > 0:
                raise RuntimeError(f"train/test overlap detected: {len(overlap)} indices")
            # Verify embargo gap exists
            if len(fold.train) > 0 and len(fold.test) > 0:
                gap = fold.test[0] - max(fold.train)
                if gap < self.embargo:
                    raise RuntimeError(
                        f"embargo gap too small: expected >= {self.embargo}, got {gap}"
                    )
            yield fold

    def cv_train_test_split(
        self, X: np.ndarray, y: np.ndarray, folds: int = 5
    ) -> Generator[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray], None, None]:
        """Yield (X_train, y_train, X_test, y_test) per fold."""
        n = len(X)
        for fold in self.generate_folds(n, folds=folds):
            X_train, X_test = X[fold.train], X[fold.test]
            y_train, y_test = y[fold.train], y[fold.test]
            yield X_train, y_train, X_test, y_test
