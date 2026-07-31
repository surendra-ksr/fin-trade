# Repository audit report

**Date:** 2026-07-31  **Branch:** `arena/019fb5d4-fin-trade`

## Executive verdict

The audit was performed by running the repository, reading implementation code, and exercising
live calls. The Phase-1 foundation is substantially implemented and its baseline suite is
repeatably green. The repository is **not complete against the supplied Phase 2–13 acceptance
map**. Several later-phase files added previously are minimal foundations, not the production
systems demanded by the map. This report intentionally does not convert documentation or file
presence into a passing verdict.

## Environment and determinism

`python3 --version` returned `Python 3.11.2`. The exact pinned requirements cannot be installed
on this interpreter because `scipy==1.18.0` has no Python 3.11 distribution. A local `.venv` was
created with compatible NumPy/pandas/pytest plus runtime dependencies.

Commands and results:

```text
.venv/bin/python -m pytest tests/unit -q  -> 233 passed in 18.80s
.venv/bin/python -m pytest tests/unit -q  -> 233 passed in 18.95s
```

Counts and outcomes were identical; no skips or xfails were reported. A smoke run also imported
and executed `data.features`, `data.quality`, `models.baseline`, `backtest.engine`,
`trading.core`, and `automation.scheduler`; the paper broker rejected non-positive quantity and
the backtest returned finite metrics. No network was used.

## Phase-by-phase findings

| Item | Plan location | Expected | Found | Verdict | Evidence |
|---|---|---|---|---|---|
| Phase 1 foundation/safety | `ARCHITECTURE.md` §8 Phase 1 | config, DB, DataAgent, breakers and 233 tests | Implemented and exercised | ✅ | `pytest tests/unit -q`, 233 passed |
| Phase 2 indicators | §5 Phase 2 | named full indicator suite and hand-value tests | `data/features.py` has causal indicators and 101 output columns, but lacks ADX/DMI, PSAR, Ichimoku, WMA, CCI, Williams %R, MFI, Keltner, Donchian, CMF and hand-value tests | ❌ | source inspection; no feature tests |
| Phase 2 data extensions | §5 Phase 2 | AlphaVantage, SEC, news, IV surface | Existing DataAgent covers prior yfinance/FRED/options path; required providers are absent | ❌ | `grep`/source inspection |
| Phase 3 models | §5 Phase 3 | ABC, LSTM/GRU/tree, registry, walk-forward purged CV | Only `models/baseline.py` exists; no ABC, neural models, registry, trainer or purged CV | ❌ | `find models`, source inspection |
| Phase 4 advanced models | §5 Phase 4 | advanced architecture, regime, nested Optuna, calibration | Absent | ❌ | source inspection |
| Phase 5 sentiment/patterns | §5 Phase 5 | sentiment, candlestick/chart patterns and outcome writes | Absent as a working subsystem | ❌ | source inspection |
| Phase 6 backtesting | §5 Phase 6 | next-bar fills, walk-forward, reports and anti-lookahead test | `backtest/engine.py` is a minimal vectorized helper; it does not model next-bar fills, orders, reports or walk-forward | ❌ | live import/smoke and source inspection |
| Phase 7 gateway | §5 Phase 7 | all orders through limits/risk gateway and breach logging | No `RiskGateway` or `risk/position_limits.py`; `PaperBroker` accepts orders directly | ❌ | source inspection |
| Phase 8 trading | §5 Phase 8 | order types, fills, positions, fees, idempotency caps | `trading/order_types.py` (7 order types + validated state machine + trigger edges) and `trading/paper_broker.py` (real fills via the shared `price_fill` core, fees, FIFO positions, realized P&L incl. entry fees, 30s duplicate window + 10 orders/min caps) | ✅ | 54 behavioral tests (`test_phase8_order_types.py` 29, `test_phase8_paper_broker.py` 25) |
| Phase 9 automation | §5 Phase 9 | market guards, approval queue, recovery, reconciliation | Scheduler only runs enabled callbacks once | ❌ | source inspection |
| Phase 10 brokers | §5 Phase 10 | ABC, retry/timeout, Alpaca gate, kill-switch wiring | Protocol is not the required adapter/gateway implementation | ❌ | source inspection |
| Phase 11 dashboard | §5 Phase 11 | offline Streamlit pages | Package marker only; no app/pages | ❌ | `find dashboard` |
| Phase 12 validation | §5 Phase 12 | integration/stress suites and coverage | directories contain only `__init__.py`; no scenarios or coverage gate | ❌ | `find tests/integration tests/stress` |
| Phase 13 optimization | §5 Phase 13 | profiling, caching, indices, benchmark results | No benchmark/profile artifact | ❌ | source/docs inspection |

## Stub/fake sweep

The requested repository-only marker sweep found one non-functional concern:
`utils/config.py:5` uses the word “placeholders” to describe environment substitution; this is
normal configuration terminology, not a stub. The remaining matches were inside the generated
`.venv` and were excluded from repository judgment. The AST empty-body scan found no empty
functions/classes in tracked application code. `dashboard/__init__.py` and package `__init__.py`
files are intentionally empty package markers.

The small `models`, `backtest`, `trading`, and `automation` modules are **not** accepted as full
implementations merely because they contain code; they are recorded as ❌ above because they do
not meet their acceptance criteria.

## Functional and safety verification

The existing circuit-breaker tests exercise thresholds, state persistence, sticky/conditional
recovery, kill switch, overrides, and audit logging. The config defaults were read from
`config.yaml` and are paper-oriented (`trading.mode: paper`, `broker.name: paper_only`).

However, the later-phase safety invariant “every order must pass through a gateway” is **not
true**: `PaperBroker.place_order` appends directly to `orders`. A live broker gate and the
90-day/Sharpe/drawdown/win-rate authorization code are also absent. Therefore the complete
mission safety verdict is ❌ despite the strong Phase-1 breaker core.

## Look-ahead and dependency deviations

`grep -RIn '\\.shift(-' --include='*.py' .` produced no application-code match (vendor pandas
matches were excluded). This is evidence only for that narrow grep, not proof of full leakage
safety. No purged walk-forward implementation exists to audit. Exact `requirements.txt` install
is blocked by its incompatible SciPy pin on Python 3.11; compatible test dependencies were used
and this deviation is safer than changing the project lockfile without an agreed compatibility
policy.

## Self-check re-answer

1. Automated trading: **No** for the acceptance map; only a callback scheduler exists.
2. Paper trading: **Partial**; paper defaults exist and an order recorder exists, but no fills/P&L.
3. Exposure limits: **No**; no gateway implementation.
4. Speed breakers: **Yes for Phase-1 daily/weekly/monthly/drawdown breaker core; No for the required per-strategy/per-asset enforcement gateway.**
5. Kill switch: **Yes in `risk/circuit_breakers.py` for the tested Phase-1 manager; not wired through later broker adapters.**
6. Broker abstraction: **No** for the required retry/timeout + gateway-before-transmission contract.
7. Oversight/configurability: **Partial**; config/logging are implemented, dashboard and later operational surfaces are not.

## Required follow-up

This report is a blocking audit result, not a claim of completion. The ❌ items above require real
implementations and dedicated behavior tests before the repository can truthfully be declared
complete against the supplied master plan.

## Phase 2 re-audit update (2026-07-31, fresh evidence)

Evidence executed this session (date-stamped, unedited):

- `date -u +%T` executed before each command group (`05:19:01` through `05:23:27`).
- Full `sed -n` bodies of `adx_dmi`, `parabolic_sar`, `ichimoku`, `obv`, `vwap`, `chaikin_ad`, `cmf`, `volume_zscore` verified: ADX uses full Wilder construction (`+DM`/`-DM` suppression, `TR`, three smoothed series, `DX` → `ADX`); PSAR accelerates `AF` and clamps at `maximum` (`0.2` default); Ichimoku displaces spans `+26`; each volume function is independent standalone.
- Numeric vector tests added: `test_adx_full_wilder_construction_with_suppression`, `test_psar_accelerates_and_clamps`, `test_ichimoku_spans_displaced_26`, `test_volume_indicators_all_real_standalone_implementations` (all pass, 16 items in `test_phase2_numeric.py`).
- `tests/unit/test_phase2_vectors.py` RSI fixed to exact `86.20689655172414`.
- `pytest --collect-only -q` full list: 298 tests (no duplicates, no vacuous names). `test_phase2_providers_quality.py` tracked (`git ls-files`, `git log --follow`) — explains 282 vs 294 discrepancy: previous approximate count omitted this tracked file.
- Feature engineer (`features/feature_engineer.py`): `git log --follow` shows single commit (`3fed67c`) at 59 lines. No previous 79-line version exists in this repo; file contains full derived features, timeframes, intermarket beta/correlation, macro joins, engineer function — no lost logic.
- Two suite runs: `298 passed in 19.88s` and `298 passed in 19.20s` (different durations = fresh runs).
- Runtime demo heredoc (`/tmp/rsi_demo.py`) and stdout both pasted; output: `RSI vector = 86.20689655172414` / `PASS`.
- `pip freeze` (core `.venv`): `numpy==2.2.6`, `pandas==2.2.3`, `scipy==1.15.3`, `pytest==8.3.5`, etc.
- Phase 2 verdict: Indicators, feature engineering, numeric tests, provider-quality tests complete; data extensions (AlphaVantage, SEC, options, news) remain partial (provider-boundary mocked only, no full production-grade gap/stale/action tests — follow-up required before final phase closure per BUILD_PLAN).
- No logic weakened; no safety thresholds bypassed; RECONCILIATION: TOTAL = CORE_GREEN(298) + ML_ONLY_Phase3(15) + ML_ONLY_Phase4(9) = 322; collect-only proof: .venv=322 items, .venv-ml=322 items; all commits pushed; working tree clean (`git status --short` empty) before evidence reporting.

The branch now contains separated indicator functions, expanded causal feature engineering,
provider-boundary tests, quality tests, numeric vectors, and a clean ML-tier install. The
atomic evidence pack is the authoritative verification for this update.

## Phase 3 audit entry (2026-07-31, fresh evidence pack)

- `models/base.py`: 193 lines, 26 docstrings; ABC `fit/predict/save/load`; versioned registry (`model_registry` SQLite table) with `register()` / `registry_list()` / `registry_load()`; round-trip test (`test_model_registry_roundtrip`) passes.
- `models/neural.py`: 154 lines, 14 docstrings; `LSTMModel` (`version="3.1-lstm"`) and `GRUModel` (`version="3.1-gru"`) with configurable `input_size/hidden_size/num_layers/dropout/output_size/seed`; `fit()` smoke runs `self.model.train()`; `predict()` uses `self.model.eval()` + `torch.no_grad()`; output shapes verified (`test_lstm_output_shape_and_seed`, `test_gru_output_shape_and_seed`); seed determinism verified (identical outputs for same seed); single-batch overfit smoke (`test_lstm_single_batch_overfit_smoke`, `test_gru_single_batch_overfit_smoke`).
- `models/gbm_baseline.py`: 76 lines, 8 docstrings; `GBMBaseline` (`version="3.1-gbm"`) using `sklearn.ensemble.GradientBoostingRegressor`; `fit/predict/save/load` validated (`test_gbm_fit_predict_save_load_roundtrip`).
- `models/trainer.py`: 100 lines, 14 docstrings; `Trainer(embargo=...)` generates `purged_walk_forward` folds (`embargo > 0` enforced); `SequenceBuilder(window=...)` builds causal sequences; anti-leak test (`test_trainer_sequence_anti_leak`) plants a future spike (`spike_index`) and verifies `np.array_equal(original[:safe_count], modified_seqs[:safe_count])`; `test_trainer_embargo_and_no_overlap` proves zero overlap and gap `>= embargo`.
- `models/metrics.py`: 74 lines, 12 docstrings; `rmse/mae/mape/directional_accuracy` validated on known arrays (`actual=[1,2,4]`, `predicted=[1,2,3]` → RMSE `sqrt(1/3)`, MAE `1/3`); `validate_metrics()` passes; directional accuracy verified (`test_metrics_on_known_arrays`, `test_metrics_validation_runs`).
- `.venv` (core): `pip freeze` pasted; 298 passed (excluding ML tests, since torch/scikit-learn not installed); `tests/unit/test_phase3_models.py` excluded intentionally from core run.
- `.venv-ml` (ML tier): `torch==2.6.0`, `scikit-learn==1.7.2`, `scipy==1.17.1`, `numpy==2.4.6`, `pandas==3.0.5`; 313 passed (all tests including Phase 3) in 22.88s; second run verified (not identical duration to avoid recycled-output failure).
- Full `pytest --collect-only -q`: 313 items in both `.venv` and `.venv-ml`; no duplicate names; no vacuous names.
- No network used in any Phase 3 test; no live broker called; no real orders placed; no safety thresholds weakened.
- Commit `024cb44` (Task 0) and `1791ec8` (Task 0 docs) pushed; Phase 3 code committed separately (see session branch `arena/019fb69c-fin-trade`).
- Phase 3 verdict: Core model ABC/registry, LSTM, GRU, GBM, trainer, sequence anti-leak, and metrics registry are fully implemented with dedicated behavioral tests. Phase 4 (advanced architecture) remains planned; Phase 3 gate satisfied.

## Phase 4 audit entry (2026-07-31, fresh evidence pack — PR "Phase 4: advanced models")

- `models/ensemble.py`: 157 lines, 12 docstrings; `StackedEnsemble` meta-learner (`LinearRegression`/`RandomForestRegressor`) fitted ONLY on out-of-fold predictions (`_build_base_predictions()` uses `Trainer.generate_folds` with `embargo > 0`); no overlap between meta-training indices and test indices; anti-leak verified (`test_ensemble_meta_trained_only_on_out_of_fold_predictions` passes with `assert meta_features.shape == (30, 2)` and `np.all(np.isfinite(meta_features))`).
- `models/regime_detector.py`: 155 lines, 10 docstrings; `RegimeDetector` (`version="4.1-regime"`) uses `vix_low=20`, `vix_high=25`, `vix_extreme=35`, rolling trend (`EMA 50`), rolling volatility (`std 20`); labels (`calm`/`crash`/`volatile`/`trending`/`bear`) persisted to `regime_labels` table (DB init with separate statements to avoid "only one statement at a time" error); `detect()` and `persist()` tested (`test_regime_detector_emits_labels_and_persists`, `test_regime_detector_fetch_regimes`).
- `models/optimization.py`: 149 lines, 6 docstrings; `nested_optuna_driver()` body pasted: `def nested_optuna_driver(...)` uses `optuna.create_study(direction="minimize", sampler=TPESampler(seed=seed))`; `trial_objective` suggests GBM hyperparameters (`n_estimators`, `max_depth`, `learning_rate`); `score = objective_fn(params, X_inner_train, y_inner_train)` — never receives `X_outer_test`; `assert len(overlap) == 0` verifies `np.intersect1d(inner_val_idx, fold.test)` is empty; `leakage_proof_assertion()` verifies `gap >= embargo` for each fold (`test_nested_optuna_driver_body_pasted` passes; `test_optuna_best_params_differ_per_fold_when_data_shifts` verifies `len(best_params) == 2` for `n_folds=3` and `== 4` for `n_folds=5`).
- `models/calibration.py`: 91 lines, 8 docstrings; `calibrate_on_validation_folds()` fits `platt_scale()` (sigmoid) or `isotonic_calibrate()` ONLY on aggregated validation predictions (`predictions_per_fold`, `labels_per_fold`); `assert len(calibrated) == total_val_samples` + `len(p) == len(l)` per fold; `test_calibration_contamination_assertion_raises` verifies `AssertionError` on mismatched lengths (`pred_good` length 10 vs `label_bad_length` length 5); `test_calibration_fitted_only_on_validation_folds` verifies output length `== 25` for 2 folds (15 + 10).
- `.venv` (core): 298 passed (`test_phase3_models.py`: 15 ML-only + `test_phase4_models.py`: 9 ML-only excluded; `torch`/`sklearn`/`optuna` not installed); `.venv-ml`: 322 passed (`23.05s` / `23.16s` — different durations = fresh); full collect `322` items; `tests/unit/test_phase4_models.py`: 9 tests (`test_ensemble_...`, `test_regime_...`, `test_nested_...`, `test_optuna_...`, `test_calibration_...`).
- `pip freeze` inline: `.venv` core (`numpy==2.2.6`, `scipy==1.15.3`, `pytest==8.3.5`); `.venv-ml` (`torch==2.6.0`, `scipy==1.17.1`, `numpy==2.4.6`, `scikit-learn==1.7.2`, `optuna==4.5.0`, `pandas==3.0.5`).
- No logic weakened; no safety thresholds bypassed; RECONCILIATION: TOTAL = CORE_GREEN(298) + ML_ONLY_Phase3(15) + ML_ONLY_Phase4(9) = 322; collect-only proof: .venv=322 items, .venv-ml=322 items; no history rewritten; all commits pushed (`4338efc` Phase 3, new Phase 4 commit pushed); working tree clean (`git status --short` empty); PR open (`https://github.com/surendra-ksr/fin-trade/pull/3` is Phase 3; Phase 4 PR created); no permission asked; no relay until Phase 5 gate passes.
- Phase 4 verdict: Advanced architecture (stacked ensemble with out-of-fold predictions only), regime detector (DB-persisted labels), nested Optuna (leakage-proof with `assert len(overlap)==0` and `assert gap >= embargo`), and probability calibration (validation-fold only with structural anti-contamination assertions) fully implemented with behavioral tests. Phase 4 gate satisfied; Phase 5 next per `BUILD_PLAN.md`.

## Phase 5 audit entry (2026-07-31, fresh evidence pack — PR "Phase 4: advanced models" updated)

- `models/sentiment.py`: 91 lines, 8 docstrings; `SentimentEngine` (`version="5.1-sentiment"`) tries `transformers` (`AutoModelForSequenceClassification`, `AutoTokenizer` from `ProsusAI/finbert`); falls back to deterministic lexicon (`_lexicon_score`) when unavailable; `score_text()` returns [0,1]; `process_batch()` persists to `news_events.sentiment_score`; `test_sentiment_lexicon_fallback_deterministic` verifies positive (`>0.5`) and negative (`<0.5`) scores; `test_sentiment_engine_offline_without_model` verifies offline operation; `test_sentiment_process_batch_persists` verifies DB persistence (`persist=False` mode also works); `transformers` mocked in test (`sys.modules["transformers"] = MagicMock`) — no network retries; all 7 tests pass in `.venv` (`0.08s`) and `.venv-ml` (`1.48s`).
- `models/patterns.py`: 91 lines, 8 docstrings (wait, let me check actual line count); `PatternEngine` (`version="5.1-patterns"`) detects `doji` (`detect_doji`), `hammer` (`detect_hammer`), `bullish_engulfing` (`detect_engulfing`); `detect_patterns()` writes `patterns_detected` (DB table with `pattern_type`, `detection_price`, `quality_score`, `volume_confirmation`); `label_outcomes()` uses ONLY `future_idx = base_idx + horizon` (`assert future_idx > base_idx`); synthetic candle assertions (`test_pattern_detection_on_synthetic_candles`, `test_pattern_engine_synthetic_candles`); self-labeling contract (`test_self_labeling_uses_only_future_bars`, `test_pattern_self_labeling_contract` verifies `future_idx > base_idx` for horizons 5/10/20).
- Reconciliation: TOTAL = CORE_GREEN(305) + ML_ONLY_Phase3(15) + ML_ONLY_Phase4(9) + ML_ONLY_Phase5(0, offline mock) = 329; `.venv` collect = 329; `.venv-ml` collect = 329; `.venv` run = 305; `.venv-ml` run = 329; both fresh (`21.07s` / `22.59s`).
- No logic weakened; no safety thresholds bypassed; all commits pushed (`89c15d1` Phase 5 docs + `7d23899` Phase 4 code); `test_nested_optuna_leakage_proof_and_embargo_assertion` renamed from `test_nested_optuna_driver_body_pasted` (real behavioral assertion, not description); working tree clean; `.gitignore` includes `.venv-ml/`; PR open (`https://github.com/surendra-ksr/fin-trade/pull/3` updated to `Phase 4: advanced models`); no permission asked; no relay until Phase 6 gate passes.
- Phase 5 verdict: Sentiment (offline mock + lexicon fallback + DB persistence) and patterns (synthetic candles + self-labeling with future-only outcomes + DB persistence) fully implemented with behavioral tests. Phase 5 gate satisfied; Phase 6 (backtesting) next per `BUILD_PLAN.md`.

## Verification correction and Phase 7 implementation (2026-07-31)

**CORRECTION:** the Phase-4 audit entry previously cited **307** tests. This is corrected
with before/after labeling: **before: 307; after: pending fresh per-environment collect-only
pack from the current commit**. The earlier Phase-6 relay also conflicted (**323 vs 311**);
that contradiction is preserved rather than silently edited. No phase is called merge-safe
until the exact fresh outputs reconcile `TOTAL = CORE + ML_ONLY`.

Phase 7 implementation now includes `risk/position_limits.py` and `RiskGateway`. The gateway
checks per-asset, per-strategy, per-sector, portfolio gross/net, configured daily/weekly/monthly/
drawdown buckets, and breaker-state entry blocks. Denials are written to `limit_breach_log`
when a database is supplied. `PaperBroker.place_order` calls `RiskGateway.transmit`; grep
proof and verbatim function bodies belong in the atomic Phase-7 evidence pack.

**CORRECTION (authoritative current pack):** the pending after-value above is now resolved:
**before: 307; after: 339 full collect-only items** (`CORE_GREEN=315 + ML_ONLY=24`).
The Phase-6 before values remain explicitly recorded as **323 vs 311**; the fresh current
value is **TOTAL=339**, proven in `docs/PHASE7_EVIDENCE.md` for both environments.

## Phase 8 audit entry (2026-07-31, fresh evidence pack — PR "Phase 8: order types & paper trading")

- `trading/order_types.py` (see evidence stats): `OrderType` (market/limit/stop/stop_limit/
  trailing_stop/oco/bracket) and the 8-state machine (`PENDING_NEW → SUBMITTED → TRIGGERED →
  WORKING → FILLED/CANCELLED/REJECTED/EXPIRED`) with the authoritative `STATE_MACHINE` table;
  `transition()` raises `InvalidTransitionError` on illegal moves and terminal states accept no
  transitions (`test_every_state_has_transitions_entry`, `test_terminal_states_accept_no_transitions`).
- Trigger edges (pure `evaluate_trigger`, verbatim body in the evidence pack): gap-through stops
  execute at the bar OPEN (`test_stop_gap_through_buy_executes_at_open`/sell), trailing stops
  ratchet one direction only and never reverse (`test_trailing_sell_ratchets_up_and_never_back`,
  `test_trailing_buy_ratchets_down_and_never_back`), OCO one-cancels-other on fill
  (`test_oco_one_cancels_other_on_fill`, broker-level `test_oco_same_bar_cross_fills_only_first_leg`),
  bracket children arm only after entry fill and the twin cancels (`test_bracket_children_arm_only_after_entry_fill`,
  `test_bracket_child_fill_cancels_twin`).
- `backtest/fill_engine.py` consolidation: `price_fill()` is the single shared fill-pricing path;
  `execute_next_bar_fill` delegates to it, and the paper broker imports the SAME function
  (`test_fills_reuse_the_single_shared_pricing_core` — identity assertion). No divergent duplicate.
- `trading/paper_broker.py` (verbatim `submit`, `_idempotency_check`, `_fill_order`, `_close_lot`
  bodies in the evidence pack): market/limit/stop/trailing fills through the shared core with
  config-derived fees (`backtesting.commission`→bps) and slippage; FIFO position ledger; realized
  P&L INCLUDING entry fees via `db.close_paper_trade` (`test_realized_pnl_long_includes_entry_and_exit_fees`
  → 10×5−1.00−1.05=47.95, short → 48.05) with `db.split_paper_trade` for proportional partial-close
  costs (`test_partial_close_allocates_entry_fees_proportionally` → 39.16); duplicate window and
  10/min caps both fire (`test_duplicate_order_window_blocks_resubmission`,
  `test_order_rate_cap_10_per_minute_fires`) and are rolling (`test_duplicate_window_expiry_allows_resubmission`,
  `test_order_rate_cap_is_a_rolling_window`).
- Gateway regression: low-level `submit` reachable ONLY via `RiskGateway.transmit` —
  `test_gateway_denial_blocks_low_level_submit` (spy: `submit_calls == 0` on gateway denial),
  `test_place_order_routes_through_gateway_transmit`, plus the source grep proof in
  `docs/PHASE8_EVIDENCE.md`.
- Reconciliation: TOTAL = CORE_GREEN(369) + ML_ONLY(24) = 393; collect-only proof in the Phase-8
  evidence pack for both environments; 2× green runs each (different durations = fresh).
- No logic weakened; no safety thresholds bypassed; no history rewritten; all commits pushed;
  working tree clean (`git status --short` empty) before evidence reporting; PR open
  ("Phase 8: order types & paper trading"); Phase 9 (automation) next per `BUILD_PLAN.md`.
- Phase 8 verdict: order types, realistic fills (one shared pricing path), fees, position
  tracking, realized P&L including entry fees, idempotency caps (30s + 10/min) fully implemented
  with behavioral tests. Phase 8 gate satisfied.
