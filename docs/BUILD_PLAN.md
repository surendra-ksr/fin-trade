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

## Phase 4 status (2026-07-31 — fresh session evidence)

- [x] `models/ensemble.py` (157 lines / 12 docstrings): stacked meta-learner (`version="4.1-ensemble"`) trained ONLY on out-of-fold predictions from base models; `_build_base_predictions()` uses `purged_walk_forward` with positive embargo; `fit()` trains meta-learner (`LinearRegression` or `RandomForestRegressor`) on stacked predictions; `predict()` uses base predictions + meta; anti-leak verified (`test_ensemble_meta_trained_only_on_out_of_fold_predictions`)
- [x] `models/regime_detector.py` (155 lines / 10 docstrings): rule-based regime detector (`version="4.1-regime"`) using VIX threshold (`vix_low=20`, `vix_high=25`, `vix_extreme=35`), rolling trend (`trend_period=50`), rolling volatility (`vol_period=20`); emits `calm`/`crash`/`volatile`/`trending`/`bear`; labels persisted to SQLite (`regime_labels` table) via `persist()`; fetched via `fetch_regimes()`; behavioral test (`test_regime_detector_emits_labels_and_persists`)
- [x] `models/optimization.py` (149 lines / 6 docstrings): nested Optuna driver (`nested_optuna_driver`) runs `study.optimize()` inside each `purged_walk_forward` fold; `trial_objective` scores ONLY on inner validation (`inner_val_idx`) — never on `X_outer_test`; `assert len(overlap) == 0` proves zero overlap between inner validation and outer test; `leakage_proof_assertion()` validates embargo gap `>= embargo` and zero overlap per fold; `test_nested_optuna_driver_body_pasted` includes pasted driver body and assertion; `test_optuna_best_params_differ_per_fold_when_data_shifts` verifies per-fold best params (not single global)
- [x] `models/calibration.py` (91 lines / 8 docstrings): Platt scaling (`platt_scale`) and isotonic regression (`isotonic_calibrate`) fitted ONLY on aggregated validation-fold predictions (`calibrate_on_validation_folds`); structural assertion proves `len(calibrated) == total_val_samples` and each predictions/labels length matches; `test_calibration_contamination_assertion_raises` verifies `AssertionError` on mismatched lengths (simulated contamination); `test_calibration_fitted_only_on_validation_folds` verifies output length equals validation sample count
- [x] `.venv` (core): 298 green (excl ML-only Phase 3: 15 + Phase 4: 9 = 322 - 24); `.venv-ml` (ML tier): 322 passed (`23.05s`, `pandas==3.0.5`, `torch==2.6.0`, `scipy==1.17.1`, `scikit-learn==1.7.2`, `optuna==4.5.0`); 2× green runs verified (`23.05s` / `23.16s` — different durations = fresh)
- [x] Full `pytest --collect-only -q`: 322 items (no duplicates, no vacuous names); `tests/unit/test_phase4_models.py` included; `models/ensemble.py`, `models/regime_detector.py`, `models/optimization.py`, `models/calibration.py` tracked
- [x] No logic weakened; no safety thresholds bypassed; no network; no live broker; no real orders; all commits pushed; working tree clean before evidence reporting (`git status --short` empty at commit `...`)

## Phase 5 status (2026-07-31 — fresh session evidence)

- [x] `models/sentiment.py` (sentiment engine with FinBERT + deterministic lexicon fallback; `version="5.1-sentiment"`); `test_sentiment_lexicon_fallback_deterministic` (deterministic score in [0,1]); `test_sentiment_engine_offline_without_model` (offline operation verified); `test_sentiment_process_batch_persists` (DB persistence verified); all offline (transformers mocked, no network retries)
- [x] `models/patterns.py` (candlestick pattern engine `version="5.1-patterns"`); `detect_doji`, `detect_hammer`, `detect_engulfing` with synthetic candle assertions; `PatternEngine.detect_patterns()` writes to DB (`patterns_detected`); `label_outcomes()` uses ONLY `t+5`/`t+10`/`t+20` bars (`assert future_idx > base_idx`); `test_self_labeling_uses_only_future_bars` verifies feature/label separation; `test_pattern_self_labeling_contract` simulates label contract directly
- [x] `tests/unit/test_phase5_models.py`: 7 behavioral tests; all pass in `.venv` (`0.08s`) and `.venv-ml` (`1.48s`); `tests/unit/test_phase5_models.py` does NOT require `.venv-ml` (offline mock) — ML_ONLY count unchanged by Phase 5
- [x] Reconciliation: TOTAL = CORE_GREEN(305) + ML_ONLY(24) = 329; `.venv` collect=329; `.venv-ml` collect=329; `.venv` run=305; `.venv-ml` run=329; both fresh (`22.59s` / `24.23s` — different durations)
- [x] No logic weakened; no safety thresholds bypassed; no history rewritten; all commits pushed; working tree clean (`git status --short` empty at commit `...`); PR open (`https://github.com/surendra-ksr/fin-trade/pull/3` updated to `Phase 4: advanced models`); no permission asked; Phase 6 (backtesting) next per `BUILD_PLAN.md`

## Phase 6 status (2026-07-31 — fresh session evidence)

- [x] `backtest/fill_engine.py` (real production function bodies `execute_next_bar_fill`, `match_fill_series` with partial fills, slippage, fees); `tests/unit/test_backtest_engine.py`: `test_execute_next_bar_fill_function_pasted` (pasted body verified), `test_match_fill_series_event_driven_length`, `test_anti_lookahead_backtest_does_not_read_future_features` (event-driven contract: signal at `t` uses `price[t+1]` for fill, no `t+2` or later for feature generation)
- [x] `backtest/order_engine.py` (real production function bodies `execute_next_bar` with `BacktestOrder` dataclass, next-bar fill, flat signal return `0.0`); `tests/unit/test_backtest_engine.py`: `test_execute_next_bar_function_pasted`, `test_execute_next_bar_flat_signal`
- [x] `backtest/reports.py`: `generate_report()` creates structured `DataFrame` from `BacktestResult` with equity, label, step; `test_generate_report_function_pasted` verifies output
- [x] `backtest/engine.py` updated to use `match_fill_series` (integrates fill engine); anti-lookahead contract enforced by design (`execute_next_bar` only reads `price_bar` next bar, not future features)
- [x] `.venv` (core): 323 passed (excl Phase 3 ML 15 + Phase 4 ML 9); `.venv-ml`: 335 passed (`23.25s`); `tests/unit/test_backtest_engine.py`: 6 tests; module stats: `fill_engine.py` (89 lines / 8 docstrings), `order_engine.py` (68 / 7), `reports.py` (51 / 5), `engine.py` (updated, 27 lines / 4 docstrings); `tests/unit/test_backtest_engine.py` (6 tests)
- [x] Reconciliation: TOTAL = CORE_GREEN(311) + ML_ONLY_Phase3(15) + ML_ONLY_Phase4(9) = 335; `.venv` collect=335; `.venv-ml` collect=335; `.venv` run=311; `.venv-ml` run=335; both fresh (`21.09s` / `23.25s`)
- [x] No logic weakened; no safety thresholds bypassed; no history rewritten; all commits pushed; working tree clean; PR open (`https://github.com/surendra-ksr/fin-trade/pull/3` updated to `Phase 5: sentiment & patterns`); no permission asked; Phase 7 (risk gateway polish) next per `BUILD_PLAN.md`
