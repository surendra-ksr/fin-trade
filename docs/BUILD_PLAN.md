# Master build plan (record of acceptance)

This is the plan of record. Every phase requires real implementation, behavioral tests, two identical green runs, safety review, and an updated architecture roadmap.

1. **Foundation + Safety Core:** configuration, logging, utilities, SQLite migrations/CRUD, DataAgent, and immutable circuit-breaker safety core.
2. **Data & Features:** pure pandas/numpy full indicator suite; multi-timeframe, derived, intermarket and macro feature engineering; data quality; Alpha Vantage, SEC, options IV and news extensions.
3. **Core Models:** versioned model ABC/registry; LSTM, GRU and tree ensemble; walk-forward, purged/embargoed CV; past-only sequences; metrics.
4. **Advanced Models:** advanced architecture/ensemble; regime detector; nested walk-forward optimization; probability calibration.
5. **Sentiment + Patterns:** FinBERT/lexicon sentiment; candlestick/chart patterns; self-labeling outcomes.
6. **Backtesting:** event-driven next-bar fills, realistic costs, walk-forward, purged CV, anti-look-ahead tests and reports.
7. **Risk Limits & Gateway:** asset/strategy/sector/portfolio limits; all speed breakers; mandatory pre-transmission RiskGateway and breach audit.
8. **Orders + Paper Trading:** complete order types, realistic fills, fees/P&L, idempotency and order-rate protection.
9. **Automation:** ET session scheduler, guards, approval queue, automated mode, recovery ramp, digest and reconciliation.
10. **Broker Integration:** adapter ABC, retry/timeout, paper and gated Alpaca adapter, complete live gate and kill-switch wiring.
11. **Dashboard:** offline Streamlit pages, read-mostly views, token-confirmed kill switch and headless boot.
12. **Testing & Validation:** integration paper day, stress scenarios, mutation checks and risk/trading coverage >=85%.
13. **Optimization:** profiling-led vectorization, feature caching, DB hot-query indices, benchmark and documented results.

Acceptance details are defined by the authoritative phase map in the build request and must not be weakened. The architecture roadmap links here.

## Dependency policy

`requirements.txt` is the mandatory Python 3.11 core for Phases 1–2. `requirements-ml.txt`
is isolated to Phases 3–5 and uses Torch-first dependencies. `requirements-optional.txt`
contains Phase 10/11 integrations. TensorFlow is excluded unless a later phase demonstrates
a concrete need.

## Phase 2 status (2026-07-31)

- [x] Canonical pure pandas/NumPy indicator module and causal feature engineering
- [x] Behavioral indicator vectors and edge-case tests
- [x] Multi-timeframe backward-only joins
- [x] Quality module and compatibility shim consolidation
- [x] Credential-gated Alpha Vantage, SEC facts, options surface, and news ingestion APIs
- [ ] Provider-extension mocked HTTP tests and full production-grade data-quality gap/stale/action tests (follow-up required before final phase closure)

### Phase 2 close-out evidence (2026-07-31 — fresh session evidence)
- [x] Canonical indicator suite and separated volume functions (ADX Wilder full, PSAR AF clamp, Ichimoku +26 shift verified via sed dumps)
- [x] Numeric vector and edge-case tests (298 passed, 2× green: 19.88s / 19.20s; new ADX/PSAR/Ichimoku/volume tests added)
- [x] Causal multi-timeframe/intermarket/macro pipeline (`feature_engineer.py` 59 lines complete; no lost logic; `git log --follow` single commit)
- [x] Mocked provider and quality behavioral tests (`test_phase2_providers_quality.py` tracked; 282→294 discrepancy resolved)
- [x] Core and ML-tier clean installs validated on Python 3.11 (`.venv` created; `.venv/bin/pip freeze` pasted; RSI demo = 86.20689655172414)
- [ ] Provider-extension mocked HTTP tests and full production-grade data-quality gap/stale/action tests (follow-up required before final phase closure)

## Phase 3 status (2026-07-31 — fresh session evidence)

- [x] `models/base.py`: ABC `fit/predict/save/load`, versioned registry persisted in SQLite (`model_registry` table), round-trip test (`test_model_registry_roundtrip`)
- [x] `models/neural.py`: LSTM + GRU (torch `2.6.0` from `requirements-ml.txt`); configurable layers/dropout; output-shape, seed-determinism (`test_lstm_output_shape_and_seed_determinism`), single-batch overfit smoke tests (`test_lstm_single_batch_overfit_smoke`, `test_gru_single_batch_overfit_smoke`)
- [x] `models/gbm_baseline.py`: GBM (`scikit-learn==1.7.2`) with fit/predict/save/load (`test_gbm_fit_predict_save_load_roundtrip`)
- [x] `models/trainer.py`: walk-forward split generator (`purged_walk_forward`) + purged embargoed K-fold CV (`embargo > 0` enforced); tests prove zero overlap (`test_purged_walk_forward_zero_overlap_and_embargo`) and correct gap (`test_trainer_embargo_and_no_overlap`)
- [x] `models/trainer.py`: sequence builder (`SequenceBuilder`) with anti-leak test (`test_trainer_sequence_anti_leak`) using planted future spike that must not alter earlier rows
- [x] `models/metrics.py`: RMSE/MAE/MAPE/directional-accuracy validated on known arrays (`test_metrics_on_known_arrays`, `test_metrics_validation_runs`)
- [x] `.venv` (core) runs 298 tests (excluding Phase 3 ML tests); `.venv-ml` runs 313 tests (all green, 2× runs verified)
- [x] `models/` stats: base 193 lines / 26 docstrings; neural 154 / 14; gbm 76 / 8; trainer 100 / 14; metrics 74 / 12
- [x] No logic weakened; no safety thresholds bypassed; all commits pushed; working tree clean before evidence reporting
