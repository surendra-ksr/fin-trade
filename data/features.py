"""Backward-compatible Phase 2 feature imports.

Canonical implementations live in :mod:`features.indicators` and
:mod:`features.feature_engineer`; this shim preserves older callers.
"""
from features.indicators import compute_indicators
from features.feature_engineer import engineer

add_indicators = compute_indicators

class FeaturePipeline:
    """Compatibility wrapper around the canonical causal indicator pipeline."""
    def fit(self, frame): return self
    def transform(self, frame): return compute_indicators(frame).ffill().fillna(0.0)
    def fit_transform(self, frame): return self.fit(frame).transform(frame)

__all__ = ["add_indicators", "compute_indicators", "engineer", "FeaturePipeline"]
