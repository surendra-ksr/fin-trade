"""Metrics registry with behavioral validation on known arrays."""
from __future__ import annotations
import logging
import numpy as np

_log = logging.getLogger(__name__)


def rmse(actual: np.ndarray, predicted: np.ndarray) -> float:
    """Root mean squared error."""
    a = np.asarray(actual).ravel()
    p = np.asarray(predicted).ravel()
    if a.shape != p.shape:
        raise ValueError(f"shape mismatch: actual {a.shape} vs predicted {p.shape}")
    return float(np.sqrt(np.mean((a - p) ** 2)))


def mae(actual: np.ndarray, predicted: np.ndarray) -> float:
    """Mean absolute error."""
    a = np.asarray(actual).ravel()
    p = np.asarray(predicted).ravel()
    return float(np.mean(np.abs(a - p)))


def mape(actual: np.ndarray, predicted: np.ndarray) -> float:
    """Mean absolute percentage error; safe for non-zero actuals."""
    a = np.asarray(actual).ravel()
    p = np.asarray(predicted).ravel()
    mask = a != 0
    if not mask.any():
        return float(np.nan)
    return float(np.mean(np.abs((a[mask] - p[mask]) / a[mask])) * 100.0)


def directional_accuracy(actual: np.ndarray, predicted: np.ndarray) -> float:
    """Fraction of predictions with the same sign change direction as actual.

    Computes ``sign(actual[i+1] - actual[i])`` and compares to
    ``sign(predicted[i+1] - predicted[i])``.
    """
    a = np.asarray(actual).ravel()
    p = np.asarray(predicted).ravel()
    if len(a) < 2 or len(p) < 2:
        return float(np.nan)
    actual_dir = np.sign(a[1:] - a[:-1])
    pred_dir = np.sign(p[1:] - p[:-1])
    mask = actual_dir != 0
    if not mask.any():
        return float(np.nan)
    return float(np.mean(actual_dir[mask] == pred_dir[mask]))


# ------------------------------------------------------------------
# Behavioral validation on known arrays
# ------------------------------------------------------------------

def validate_metrics():
    """Validate metrics against hand-computed arrays; raises AssertionError on failure."""
    # RMSE: predictions [1, 2, 3], actual [1, 2, 4] -> sqrt(((0)^2 + 0 + 1)/3) = sqrt(1/3)
    actual = np.array([1.0, 2.0, 4.0])
    predicted = np.array([1.0, 2.0, 3.0])
    assert abs(rmse(actual, predicted) - np.sqrt(1.0 / 3.0)) < 1e-9
    # MAE: mean(abs([0, 0, 1])) = 1/3
    assert abs(mae(actual, predicted) - 1.0 / 3.0) < 1e-9
    # MAPE: mean(abs([0/1, 0/2, 1/4])) = mean([0, 0, 0.25]) = 0.0833... * 100
    assert abs(mape(actual, predicted) - (0.25 / 3.0) * 100.0) < 1e-9
    # Directional accuracy: perfect direction match
    actual_dir = np.array([1.0, 3.0, 5.0])
    pred_dir = np.array([1.0, 2.0, 4.0])  # same direction changes
    assert directional_accuracy(actual_dir, pred_dir) == 1.0
    # Directional accuracy: opposite
    pred_opp = np.array([2.0, 1.0, 4.0])
    assert directional_accuracy(actual_dir, pred_opp) < 1.0
    _log.info("all metric validations passed")
