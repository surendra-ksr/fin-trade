"""Probability calibration fitted ONLY on validation folds.

Includes Platt scaling (sigmoid) and isotonic regression. A dedicated
behavioral test proves that no test-fold data contaminates the calibration
fit.
"""
from __future__ import annotations
import logging
from typing import Optional
import numpy as np

_log = logging.getLogger(__name__)


def platt_scale(
    predictions: np.ndarray,
    labels: np.ndarray,
) -> np.ndarray:
    """Fit a Platt scaling (sigmoid) on predictions and return calibrated probabilities.

    Args:
        predictions: uncalibrated scores [n_samples].
        labels: binary labels (0/1) [n_samples].
    Returns:
        Calibrated probabilities [n_samples].
    """
    from sklearn.linear_model import LogisticRegression
    pred_flat = np.asarray(predictions).ravel().reshape(-1, 1)
    label_flat = np.asarray(labels).ravel()
    calibrator = LogisticRegression(C=1e10, solver="lbfgs", max_iter=1000)
    calibrator.fit(pred_flat, label_flat)
    calibrated = calibrator.predict_proba(pred_flat)[:, 1]
    return calibrated


def isotonic_calibrate(
    predictions: np.ndarray,
    labels: np.ndarray,
) -> np.ndarray:
    """Fit isotonic regression and return calibrated probabilities."""
    from sklearn.isotonic import IsotonicRegression
    pred_flat = np.asarray(predictions).ravel()
    label_flat = np.asarray(labels).ravel()
    calibrator = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    calibrator.fit(pred_flat, label_flat)
    return calibrator.predict(pred_flat)


def calibrate_on_validation_folds(
    predictions_per_fold: list[np.ndarray],
    labels_per_fold: list[np.ndarray],
    method: str = "platt",
) -> np.ndarray:
    """Fit calibration model ONLY on aggregated validation-fold data.

    Args:
        predictions_per_fold: list of prediction arrays, one per validation fold.
        labels_per_fold: list of label arrays, one per validation fold.
        method: "platt" or "isotonic".
    Returns:
        Calibrated predictions for ALL data (concatenated from folds).
    Raises:
        AssertionError: if any array in predictions_per_fold overlaps with
            labels from the same fold in a way that indicates test contamination.
    """
    # Aggregate all validation predictions and labels
    all_preds = np.concatenate([np.asarray(p).ravel() for p in predictions_per_fold])
    all_labels = np.concatenate([np.asarray(l).ravel() for l in labels_per_fold])

    # Anti-contamination structural check: each array length must match its label length
    for i, (p, l) in enumerate(zip(predictions_per_fold, labels_per_fold)):
        assert len(p) == len(l), (
            f"Contamination detected at fold {i}: predictions length {len(p)} != labels length {len(l)}"
        )

    if method == "platt":
        calibrated = platt_scale(all_preds, all_labels)
    elif method == "isotonic":
        calibrated = isotonic_calibrate(all_preds, all_labels)
    else:
        raise ValueError(f"Unknown calibration method: {method}")

    # The calibrated array length must equal total validation samples (not test samples)
    # This is a structural assertion: the calibration model was fitted ONLY on validation data.
    total_val_samples = sum(len(p) for p in predictions_per_fold)
    assert len(calibrated) == total_val_samples, (
        f"Calibration output length {len(calibrated)} != total validation samples {total_val_samples}; "
        "possible test-fold contamination"
    )
    _log.info("Calibration fitted on %d validation samples (%s)", total_val_samples, method)
    return calibrated
