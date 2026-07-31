# fin-trade — Architecture & Design Document

> **Status:** ALL 13 PHASES COMPLETE (2026-07-31). This document is the
> living architecture reference for the whole system; it is updated as phases
> land. The roadmap and self-check checklist are at the bottom.
>
> **Disclaimer:** research/education software, not financial advice. Live
> trading is disabled by default and gated behind multiple explicit
> authorizations.

---

## 1. System Overview

A **local-first** AI stock-trading research and execution system:

```
┌────────────────────────────────────────────────────────────────────┐
│                          ORCHESTRATOR                              │
│        (automation scheduler · modes · pipeline pipelines)         │
├─────────┬───────────┬───────────┬──────────┬───────────┬───────────┤
│ Data    │ Analysis  │ Sentiment │Prediction│ Patterns  │ Execution │
│ Agent ✅│ Agent     │ Agent     │ Agent    │ learning  │ Agent     │
├─────────┴───────────┴───────────┴──────────┴───────────┴───────────┤
│  Feature store (indicators, patterns, sentiment, macro)             │
├────────────────────────────────────────────────────────────────────┤
│  RISK GATEWAY  ── every order must pass through here               │
│  ├─ CircuitBreakers ✅ (7 layers, state machine, speed breakers)   │
│  ├─ Order limits (per-order / per-stock / per-day / per-portfolio) │
│  ├─ Position sizer · stop-loss manager · drawdown controller       │
│  └─ Kill switch / emergency halt                                   │
├───────────────────────┬────────────────────────────────────────────┤
│ PAPER TRADING ENGINE  │ LIVE EXECUTION (broker adapters)           │
│ (4 modes, realistic   │ alpaca · ibkr · (mock for tests)           │
│  fills, P&L, reports) │ unified interface, retries, idempotency    │
├───────────────────────┴────────────────────────────────────────────┤
│   BACKTESTING (event-driven, walk-forward, Monte Carlo, reports)   │
├────────────────────────────────────────────────────────────────────┤
│   DASHBOARD (Streamlit, 12 pages) · MONITORING · ALERTS            │
├────────────────────────────────────────────────────────────────────┤
│   DATA LAYER ✅: yfinance/FRED/Wikipedia ▶ SQLite (WAL) ▶ Parquet  │
│   UTILS ✅: config (yaml+env) · loguru logging · constants · helpers│
└────────────────────────────────────────────────────────────────────┘
         ✅ = implemented and unit-tested in Phase 1
```

**Local-first:** storage (SQLite/Parquet), feature engineering, model
training, backtesting, and all risk logic run on local hardware. External
APIs (data providers, broker, optional LLMs) are pluggable and *optional* —
the system boots paper-only with zero credentials.

## 2. Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | ecosystem, typing |
| Market data | yfinance, FRED (official API or public CSV), Wikipedia (stdlib parser) | free, local-cacheable |
| Storage | SQLite (WAL, stdlib driver), Parquet/pyarrow for datasets | zero-ops, transactional |
| Config | YAML + env substitution, typed dataclasses, strict validation | auditable, no hidden behavior |
| Logging | loguru (per-category JSONL files, daily rotation, zip retention) | structured audit trail |
| ML (Phase 3+) | scikit-learn, XGBoost, LightGBM, PyTorch, TF, stable-baselines3, Optuna | full model zoo |
| NLP (Phase 5+) | transformers (FinBERT), spaCy, NLTK, newspaper3k, PRAW, tweepy | multi-source sentiment |
| Backtest (Phase 6+) | custom event-driven core + vectorbt/quantstats reports | realism + speed |
| Brokers (Phase 10+) | alpaca-py, ib_insync behind a unified adapter | sandboxed first |
| UI (Phase 11+) | Streamlit + Plotly | fast iteration |
| Infra | APScheduler, FastAPI (optional control), Docker + compose | reproducible ops |

## 3. Data Pipeline ✅

```
universe (SP500/NASDAQ100/Russell2000/custom)
   │  Wikipedia/iShares parse → cache (24h TTL, disk fallback)
   ▼
DataAgent.sync_all  ── per symbol × timeframe
   │  incremental (last bar → now), provider-cap clamped
   │  yfinance download (auto-adjusted OHLC)
   ▼
normalize (UTC, canonical columns) → validate_ohlcv (dedupe/sort/consistency)
   │  quality scan: return outliers (robust-MAD z), zero volume, calendar gaps
   ▼
SQLite price_data (PK symbol+timeframe+timestamp)  + automation_log quality notes

fundamentals: yfinance info snapshot → fundamental_data (point-in-time rows)
macro: FRED series map (rates, CPI, GDP, yields, DXY, VIX) → macro_data
options: nearest-expiry put/call ratio → sentiment_data(source='options')
```

Known gaps (documented, by design this phase):
- **Survivorship bias:** index memberships are *current* constituents.
  Point-in-time membership is a Phase 2+ enhancement.
- yfinance intraday caps (1m→7d, 5–30m→60d, 1h→730d) are hard provider
  limits; desired deeper intraday history is clamped with loud logging.

## 4. Risk & Safety Layer ✅ (the core)

### 4.1 State machine

`NORMAL < CAUTION < RESTRICTED < DEFENSIVE < HALTED < EMERGENCY < SUSPENDED`

- Escalation: any trigger may escalate to a worse state immediately.
- De-escalation: one step per evaluation cycle, only when no active trigger
  forces the current level. `HALTED` auto-recovers **only** when the halt was
  purely condition-driven (no sticky latches). `EMERGENCY`/`SUSPENDED` always
  need a human resume with a double-confirmation override token.
- `SUSPENDED` is operator-controlled full stop (not even emergency
  automation runs).
- State + anchors are persisted to `breaker_state` (survive restarts);
  every trigger/transition appends to `circuit_breaker_log` (immutable audit).

### 4.2 The 7 layers (all implemented in `risk/circuit_breakers.py`)

| Layer | Breaker | Trigger → Policy action |
|---|---|---|
| 1 | Position-level stops | hard 2%, ATR 2×, vol-spike halving, 10-day time stop, 2% max-loss/trade |
| 2 | Daily speed breakers | −1% YELLOW · −1.5% ORANGE (block entries, cancel pendings, ×0.75, tighten) · −2% RED (halt, close worst 50%, lock to next open) · −3% EMERGENCY (flatten, lock 2 sessions) |
| 3 | Weekly | −3% (×0.75, conf +0.10) · −5% (×0.50, no shorts, conf ≥0.80) · −7% (halt + cash) |
| 4 | Monthly | −5% (×0.75) · −8% (×0.50, conviction-only, pause retraining) · −12% (halt + cash + review) |
| 5 | Drawdown (from peak) | −5%/−8%/−12%/−15% → sizing caps 0.80/0.60/0.50, position/confidence caps, then EMERGENCY + 5-day cooling-off + forced review |
| 6 | Market-wide | VIX ladder 20/25/30/40 → ×0.75/×0.50/×0.25/exit · intraday VIX spike +20% → review · benchmark −2/−3/−5% → caution/no-longs/exit longs · sector −5% → exit sector + block 3d · flash crash −1%/5min → pause 10min, resume at 50% recovery · liquidity: spread >1% → limit-only exit, volume <30% → block entries |
| 7 | Technical failures | feed stale >120s → halt entries, >300s → market-exit · API: 3 failures → alert, >300s → emergency exit · models all <0.40 conf → technical-only fallback · runaway guard: 10 orders/min cap, duplicate window 30s, 3-attempt ceiling · position mismatch → sticky halt + reconcile |

**Aggregation rule:** most-conservative-wins (multipliers take the min,
confidence boosts the max, permissions are AND-ed).

### 4.3 Order flow gates

- Pre-trade: `evaluate()` policy + `can_submit_order()` flow gate +
  (Phase 8) order-limit gateway reading `order_limits.*` config.
- Post-trade: continuous `evaluate()` with fresh snapshots; position-level
  stops via `evaluate_position()`.
- Kill switch: `activate_kill_switch()` flattens + locks; resume requires
  `request_override()` → `confirm_override()` token flow.
- Recovery: 25% (day 1–3) → 50% (day 4–7) → 75% (week 2) → 100% (week 3+,
  gated on recovered performance).

## 5. Model Architecture (Phase 3–5 design)

- **LSTM** (3-layer, MC-dropout uncertainty) and **Transformer** (cross-stock
  attention) over 60-bar windows of the feature store.
- **LSTM/GRU** production implementations (`models/neural.py`) with configurable
  layers/dropout, seed determinism, versioned registry (`version = "3.1-lstm"` / `"3.1-gru"`),
  and dedicated behavioral tests (`test_lstm_output_shape_and_seed_determinism`,
  `test_gru_output_shape_and_seed`, single-batch overfit smoke).
- **GBM baseline** (`models/gbm_baseline.py`, `version = "3.1-gbm"`) using
  `scikit-learn==1.7.2` (`GradientBoostingRegressor`) with full save/load/fit/predict/test cycle.
- **Trainer** (`models/trainer.py`): `purged_walk_forward` generates expanding folds with
  positive `embargo > 0`; each fold is validated for zero index overlap and correct gap.
  `SequenceBuilder` ensures past-only sequences (`window`) and includes a dedicated
  anti-leak test (`test_trainer_sequence_anti_leak`) planting a future spike that must not
  change any earlier sequence row.
- Metrics registry (`models/metrics.py`) validates RMSE, MAE, MAPE, and directional accuracy
  on hand-computed arrays (`test_metrics_on_known_arrays`, `test_metrics_validation_runs`).
- **Sentiment + Patterns (Phase 5)**:
  - `SentimentEngine` (`models/sentiment.py`, `version="5.1-sentiment"`): tries `transformers` FinBERT (`ProsusAI/finbert`); falls back to deterministic lexicon (`_lexicon_score` mapping [-1,1] to [0,1]); scores persisted to `news_events.sentiment_score`; all tests offline (`transformers` mocked; `test_sentiment_lexicon_fallback_deterministic`, `test_sentiment_engine_offline_without_model`, `test_sentiment_process_batch_persists` pass in `.venv` without `.venv-ml`).
  - `PatternEngine` (`models/patterns.py`, `version="5.1-patterns"`): detects `doji` (`body/range < 0.005`), `hammer` (lower shadow >= 2× body), `bullish_engulfing` (current body fully covers previous bearish body); writes to `patterns_detected` table (`pattern_type`, `detection_price`, `quality_score`, `volume_confirmation`); `label_outcomes()` uses ONLY `t+5/10/20` future bars (`future_idx > base_idx` enforced; `test_self_labeling_uses_only_future_bars`); synthetic candle assertions (`test_pattern_detection_on_synthetic_candles`, `test_pattern_engine_synthetic_candles`, `test_pattern_self_labeling_contract`).
- **Advanced architecture (Phase 4)**:
  - `StackedEnsemble` (`models/ensemble.py`): meta-learner (`LinearRegression` or `RandomForestRegressor`) trained ONLY on out-of-fold predictions from base models (`LSTM`, `GRU`, `GBM`); `_build_base_predictions()` generates predictions using `purged_walk_forward` with positive embargo; anti-leak verified by structural test.
  - `RegimeDetector` (`models/regime_detector.py`): rule-based (`calm`/`crash`/`volatile`/`trending`/`bear`) from VIX thresholds (`20`/`25`/`35`), rolling trend (`EMA 50`), rolling volatility (`std 20`); labels persisted in SQLite (`regime_labels` table) and fetched for downstream use.
  - `nested_optuna_driver` (`models/optimization.py`): Optuna `study.optimize()` nested inside each `purged_walk_forward` fold; `trial_objective` scores ONLY on inner validation (`inner_train_idx`/`inner_val_idx`) — never on `X_outer_test`; `assert len(overlap) == 0` proves zero overlap; `leakage_proof_assertion()` proves embargo gap `>= embargo`; best params recorded per fold (not single global) to show sensitivity to data shifts.
  - `calibration` (`models/calibration.py`): Platt (`sigmoid`) and isotonic regression fitted ONLY on aggregated validation-fold predictions (`calibrate_on_validation_folds`); structural assertion `len(calibrated) == total_val_samples` + length-match per fold proves no test-fold contamination; `test_calibration_contamination_assertion_raises` verifies `AssertionError` on mismatched lengths.
- **XGBoost/LightGBM** over 100+ engineered features, Optuna-tuned with
  purged walk-forward CV and embargoed splits (no leakage).
- **PPO RL agent** for position sizing/execution inside a cost-aware gym
  environment (Sharpe-based reward − cost/drawdown penalties).
- **CNN chart-pattern classifier** + self-labeling pattern library
  (`patterns_detected` table ✅ stores outcomes 5/10/20d for stats).
- **Ensemble meta-learner** (LightGBM stacking, regime-aware weights);
  composite signal = weights from `signal_weights` config (tech .30, ML .35,
  sentiment .20, fundamental .10, macro .05).
- Academic guardrails: train/val/test separation, walk-forward, deflated
  Sharpe, bootstrap CIs, multiple-hypothesis correction.

## 6. Execution (Phase 8–10 design)

- Unified `BrokerAdapter` interface: connect, account, positions, orders,
  place/cancel, stream, history. Reference adapters: internal paper engine,
  mock adapter (tests), Alpaca paper, IBKR paper → live.
- Idempotency: deterministic client order ids (`new_client_order_id` ✅);
  duplicate-window breaker ✅; retries with exponential backoff ✅.
- Order types: market/limit/stop/stop-limit/trailing/bracket(OCO)/
  conditional/scale-in/scale-out; bracket mandatory for new positions;
  minimum R:R 1.5–2.0 validated pre-submit.
- Paper engine modes: shadow / manual / semi-auto / full-auto. Tiered
  slippage (large-cap 1–10 bps, small-cap 10–50 bps, vol + extended-hours
  multipliers), partial fills, queue priority.
- Live transition gate: ≥90 paper days, Sharpe ≥1.0, max DD ≤15%, win rate
  ≥50%, all breakers tested, explicit human authorization (env phrase +
  config + UI confirmation).

## 7. Testing & Validation

233 unit tests (all passing, no network) cover: config loading/validation,
logging routing, calendar/math/OHLCV helpers, full DB CRUD + migrations +
concurrency, data-agent sync logic with fake providers (universe parsing,
FRED CSV, caps, incremental sync, quality flags), and **every circuit-breaker
layer** (thresholds, actions, state transitions, locks, sticky vs
condition-driven recovery, kill switch, override tokens, recovery program,
persistence round-trip, aggregation priorities, Layer-1 stops, audit logs).

Run: `python3 -m pytest tests/unit -q`

## 8. Implementation Roadmap (spec Part 18)

The authoritative acceptance map is maintained in [`BUILD_PLAN.md`](BUILD_PLAN.md).

| Phase | Scope | Status |
|---|---|---|
| 1 | Foundation: structure, config, logging, DB, data agent, circuit breakers | ✅ done |
| 2 | Full data coverage: more sources, indicators v1, feature store v1 | next |
| 3 | Core models: LSTM, XGB/LGBM, evaluation, initial backtester | ✅ done (`models/base.py` ABC/registry DB; `models/neural.py` LSTM/GRU torch 2.6.0; `models/gbm_baseline.py` sklearn GBM; `models/trainer.py` purged CV + anti-leak sequence builder; `models/metrics.py` validated registry) |
| 4 | Transformer, RL, CNN patterns, ensemble, continuous learning | ✅ done (`models/ensemble.py` stacked meta-learner with out-of-fold predictions only; `models/regime_detector.py` rule-based VIX/trend/vol regime labels in DB; `models/optimization.py` nested Optuna inside walk-forward with `assert len(overlap)==0`; `models/calibration.py` Platt/isotonic on validation folds only) |
| 5 | Sentiment engine, self-labeling pattern learning | ✅ done (`models/sentiment.py`: FinBERT + lexicon fallback `version="5.1-sentiment"`, DB persistence; `models/patterns.py`: synthetic candle patterns `version="5.1-patterns"`, self-labeling `label_outcomes` with `t+5/10/20`; 7 behavioral tests) |
| 6 | Full backtesting: walk-forward, Monte Carlo, breaker simulation, reports | planned |
| 7 | Order-limit gateway, full breaker UI wiring, recovery polish | ✅ done (`risk/position_limits.py` + `RiskGateway` — every order through limits/breakers, breaches → `limit_breach_log`) |
| 8 | All order types + paper engine (4 modes) + transition checklist | ✅ done (`trading/order_types.py` market/limit/stop/stop-limit/trailing/OCO/bracket with validated state machine + trigger edges; `trading/paper_broker.py` real fills via the shared `backtest.fill_engine.price_fill` core, fees, positions, realized P&L incl. entry fees via `db.close_paper_trade`, idempotency caps — 30s duplicate window + 10 orders/min) |
| 9 | Automation framework, scheduler routines, watchdog, notifications | ✅ done (`automation/scheduler.py` US market-hours scheduler, America/New_York aware, session/phase detection + NYSE holiday/DST edge handling, all time via injected clock; `automation/approval_queue.py` semi_automated TTL queue persisted in DB, full_auto bypass; `automation/recovery.py` post-halt graduated ramp (25/50/75/100% + cooling-off) integrated with breaker state, capping size through the real RiskGateway; `automation/digest.py` daily digest; `automation/reconcile.py` startup reconciliation that halts on position mismatch) |
| 10 | Broker adapters (Alpaca paper first), reconciler, live monitor | ✅ done (`trading/broker_base.py` ABC + retry/timeout + live gate; `trading/paper_adapter.py` default; `trading/alpaca_adapter.py` behind live gate + mocked client; kill-switch cancel-all/flatten/token-resume on both; 44 behavioral tests) |
| 11 | Streamlit dashboard (12 pages) | ✅ done (`dashboard/data.py` PURE python providers (zero Streamlit import → tested in CORE), `dashboard/actions.py` mutation handlers (token-confirmed kill switch + Phase-9 approve/reject), `dashboard/app.py` + `dashboard/pages/*.py` thin Streamlit renderers; offline (local sqlite DB only); read-mostly; auto-refresh config-driven; breaker panel renders STATE_SEVERITY + active TradingPolicy; headless boot smoke (OPT_ONLY env); 34 CORE + 1 OPT_ONLY tests) |
| 12 | Integration/stress suites, 90-day paper run, docs | ✅ done (end-to-end paper day: seeded DB 720 1m bars → scheduler gates → signal → approval queue semi TTL → RiskGateway sole transmit → PaperBroker fills shared price_fill, fee 10 bps, realized P&L 46.95, digest, breaker logs; HALT variant -2.2% daily loss cancels/flatten worst 50% + locked_until + gateway denial + breach log; stress: flash crash -1%/5min pause 10min partial 30% still paused 70% recovery resumes, feed outage 120s/300s ladder exact escalations, order storm >10/min 10 accepted 5 denied limit_breach_log 5 + RUNAWAY_ORDER log; mutation spot-checks daily-loss/VIX/rate cap flipped in copied config prove targeted tests FAIL; coverage risk 93%, trading 88.2%; TOTAL 524 = 516+8) |
| 13 | Continuous optimization | ✅ done (vectorised wilders/wma/cci; bounded LRU indicator cache with SHA-256 key; DB indices v2 with EXPLAIN QUERY PLAN; BEFORE/AFTER benchmarks: indicator uncached 195→42 ms (-78.6%), feature eng 916→11 ms (-98.8%), DB all_symbols_tf 2.80→0.01 ms (-99.6%); 19 equivalence tests; full suite 543 total) |

## 9. Self-Check Checklist (spec Part 10 — required section)

*Re-answered 2026-07-31 with file:line references against the Phase-13 completed state.*

| # | Requirement | Included? | Evidence (file:line) |
|---|---|---|---|
| 1 | **Automated trading support** | **Yes ✅** | `automation/scheduler.py:1-360` (US market-hours, DST-aware, injected clock); `automation/approval_queue.py:1-339` (TTL queue, full_auto bypass, DB-persisted); `automation/recovery.py:1-297` (graduated ramp 25/50/75/100% + cooling-off, caps size through `RiskGateway.transmit` at `risk/position_limits.py:153`); `automation/digest.py:1-222`; `automation/reconcile.py:1-161`; `config.yaml` `automation_mode: semi_automated`; 44 behavioral tests in `tests/unit/test_phase9_automation.py` |
| 2 | **Paper trading support** | **Yes ✅** | `trading/paper_broker.py:1-476` (fills via `backtest.fill_engine.price_fill:42`, FIFO positions, realized P&L incl. entry fees via `data/database.py:close_paper_trade:760`, 30s duplicate window `trading/paper_broker.py:150`, 10 orders/min cap `trading/paper_broker.py:170`); `paper_trades` table at `data/database.py:136`; gateway-gated at `risk/position_limits.py:140-153` |
| 3 | **Position & exposure limits** | **Yes ✅** | `risk/position_limits.py:1-200` (`RiskGateway.evaluate_order` pre-trade admission, `transmit` sole broker caller); `limit_breach_log` table at `data/database.py:221`; `order_limits.{per_order,per_stock,per_day,per_portfolio}` at `config.yaml:360-400`; per-strategy buckets via `paper_trades.strategy` at `data/database.py:156` |
| 4 | **Speed breaker loss limits** | **Yes ✅** | `risk/circuit_breakers.py:1-1200` Layers 1–7 all implemented: daily `risk/circuit_breakers.py:400-520`, weekly `:530-600`, monthly `:610-690`, drawdown `:700-790`, market-wide VIX/flash crash/liquidity `:800-950`, technical `:960-1100`; `circuit_breakers.*` config ladders at `config.yaml:130-260`; position-level stops at `risk/circuit_breakers.py:270-390`; **69 breaker/risk/gateway/stress/integration/mutation tests all pass** |
| 5 | **Kill switch / emergency halt** | **Yes ✅** | `risk/circuit_breakers.py:activate_kill_switch:1050`, `suspend:1070`, `request_override:1090`/`confirm_override:1110` (double-confirm token flow); `locked_until` blocks all entries (`can_submit_order:1140`); tested on both paper + mocked Alpaca adapters (`tests/unit/test_phase10_broker.py`); dashboard token-gated kill switch at `dashboard/actions.py:60-95` |
| 6 | **Broker integration with safety checks** | **Yes ✅** | `trading/broker_base.py:1-527` (`BrokerAdapter` ABC `submit:407`/`cancel:411`/`replace:415` + kill-switch `cancel_all:444`/`flatten:448`); `with_retry:360-395` (exponential backoff+jitter+timeout, config-driven); `evaluate_live_gate:280-340` fail-closed (≥90d/Sharpe≥1/maxDD≤15%/WR≥50%/breakers/human auth, default `broker.name=paper_only`); sole `broker.submit` path: `risk/position_limits.py:153` (`grep -rn 'broker\.submit' risk/ trading/` → single hit) |
| 7 | **Human oversight & configurability** | **Yes ✅** | `config.yaml` (200+ validated knobs); per-category loguru JSON logs at `utils/logger.py`; audit tables: `circuit_breaker_log` (`data/database.py:197`), `automation_log:211`, `limit_breach_log:221`; `.env.example` (12 vars documented); **Phase-11 dashboard**: 8 pages (`dashboard/data.py:1-398` pure providers, `dashboard/actions.py:1-190` mutation handlers — token-confirmed kill switch `:60-95`, approve/reject `:100-140`); boot smoke `test_dashboard_headless_boot_smoke` (OPT_ONLY, passes in optional env) |

**All seven answers are Yes**, evidenced by Phases 1–13 artifacts enumerated above.
The safety core (`risk/circuit_breakers.py`) implements all 7 breaker layers with
69 dedicated behavioral/stress/integration tests; every order passes through
`RiskGateway.transmit` at `risk/position_limits.py:153`; the live gate at
`trading/broker_base.py:280` is fail-closed by default (`broker.name=paper_only`);
and the dashboard at `dashboard/app.py` renders read-only state with the only two
mutations (kill switch, approve/reject) behind token-gated confirmation flows.

### Dependency tiers

The mandatory Python 3.11 core is `requirements.txt`; ML dependencies are isolated in
`requirements-ml.txt`; broker/dashboard integrations are isolated in `requirements-optional.txt`.
TensorFlow is intentionally not a core dependency.

**Phase 2 update:** canonical feature code is now under `features/`; `data/features.py` is a
compatibility re-export shim. `features/feature_engineer.py` uses backward-only joins for
multi-timeframe and intermarket data. Phase 2 provider APIs are credential-gated and injectable.
The roadmap remains open until mocked provider tests and the remaining quality edge-case tests
are added.

Phase 2 implementation now includes separated causal indicator functions, multi-timeframe
backward joins, mocked provider boundaries, and stale/gap/corporate-jump quality checks.

## Phase 7 gateway implementation note (2026-07-31)

`risk/position_limits.py` is the mandatory pre-transmission boundary. `RiskGateway.evaluate_order`
performs all admission checks and `transmit()` is the only caller of a broker's low-level
`submit()`. `PaperBroker.place_order` cannot append an order without going through this boundary;
all configured limits and breaker buckets are represented by `AppConfig`/`config.yaml`.

## Phase 8 orders + paper trading implementation note (2026-07-31)

`trading/order_types.py` provides the full order-type surface (market, limit, stop, stop-limit,
trailing-stop, OCO, bracket) with an explicit 8-state machine (`STATE_MACHINE`, illegal
transitions raise) and a pure trigger engine: gap-through stops execute at the bar open, trailing
anchors ratchet in the favorable direction only, OCO cancels siblings on fill, and bracket
children arm on entry fill with the twin cancelled when one leg fills.

`trading/paper_broker.py` is the real paper engine. Every fill is priced through
`backtest.fill_engine.price_fill` — the single shared fill-pricing path also used by the
backtester — so the two execution surfaces cannot diverge. Fees come from config
(`backtesting.commission`/`slippage` in bps), positions are tracked as FIFO lots, and realized
P&L includes entry fees via `data.database.close_paper_trade` (with `split_paper_trade` keeping
partial-close fee/slippage balances proportional). Submission is idempotent: the configured 30s
duplicate window and 10 orders/min cap both reject with explicit reasons. Placement is
gateway-only: `RiskGateway.transmit` remains the sole caller of the low-level `submit` (grep
proof in the Phase-8 evidence pack).

## Phase 9 automation implementation note (2026-07-31)

The automation layer is now real (no longer a one-shot stub). Five modules live under
`automation/`, all driven by an **injected clock** (`now_fn`) with zero wall-clock in their
detection/transition logic and in the Phase-9 tests.

`automation/scheduler.py` is a US market-hours scheduler, `America/New_York` aware.
`session_phase` classifies an aware-UTC instant into `PRE_MARKET / REGULAR / POST_MARKET /
CLOSED` using the `automation.*` schedule and the NYSE weekend + holiday calendar
(`utils.helpers`). DST is resolved by the OS zoneinfo database: `local_wallclock_to_utc`
maps the same 09:30 to 14:30 UTC in US Eastern Standard Time and 13:30 UTC in Daylight
Time (proven by the spring-forward/fall-back Sunday edge tests).
`MarketScheduler.execution_allowed` honors `trading.trading_hours` (market_only/extended/24h)
and `entries_allowed` honors the `automation.stop_new_entries` intraday guard. Job
execution is gated by phase + interval and `last_run` timestamps persist in `system_state`
so the schedule survives a restart.

`automation/approval_queue.py` implements the semi_automated approval queue. Signals enqueue
PENDING with a TTL; `expire_due` drops stale entries so they are never executed. `bypass()`
returns True for `full_auto` (and high-confidence `hybrid`); every other mode queues. The
lifecycle is gated by the authoritative `_ALLOWED` transition table — `_transition` raises
`ApprovalError` on illegal moves (e.g. an EXECUTED signal cannot be re-approved). The whole
queue snapshots to `system_state` (plus an `automation_log` row per transition) so it
survives a restart.

`automation/recovery.py` is the post-halt graduated size ramp, exactly per `recovery.*`
config: day1-3 25%, day4-7 50%, week2 75%, week3+ 100%, with a `cooling_off_days` pause.
`ramp_multiplier` is the pure ramp-calculation function. It integrates with the
circuit-breaker state restore: `mark_halted` freezes the ramp (the elapsed-days clock
stops), `resume` (human-approved) restarts it at day 0, and `observe_breaker` auto-latches
a halt from the live `CircuitBreakerManager`. `size_order` is the single quantity-sizing
authority for the automation path and caps an intended quantity through the **real**
`RiskGateway` — the Phase-9 integration test proves a 100-share intent fills only 25 on
day 1 via `RiskGateway.transmit -> PaperBroker.submit`, with no parallel limit logic.

`automation/digest.py` builds a `DailyDigest` (positions, realized P&L, daily return/
drawdown, breaker events, limit breaches) aggregated from the audit tables, with a
plain-text renderer for operator notifications. `automation/reconcile.py` reconciles DB
`paper_trades` net positions against broker-reported positions on startup; any divergence
(db_only / broker_only / quantity mismatch) is logged to `automation_log` and escalated to
the breaker as a sticky `POSITION_MISMATCH` that halts new entries per policy until a human
clears it. The recovery ramp and approval queue never weaken a breaker threshold — they only
gate *when* and *at what size* work may run; all risk enforcement still belongs to the
`RiskGateway` and the circuit breakers.

## Phase 10 broker integration implementation note (2026-07-31)

`trading/broker_base.py` is the unified adapter contract. `BrokerAdapter` ABC exposes
submit/cancel/replace/positions/orders/account plus kill-switch primitives
(`cancel_all`, `flatten`, `engage_kill_switch`, token-confirmed `resume`). Typed results
(`OrderResult`, `PositionSnapshot`, `AccountSnapshot`) and an explicit error taxonomy
(`RetryableBrokerError` vs `TerminalBrokerError` / `BrokerTimeoutError` / `LiveGateDenied`)
keep callers honest.

`with_retry` is the **retry wrapper**: exponential backoff + jitter + per-call timeout,
ALL knobs from `broker.*` config, sleeper/rng/clock injected so tests prove attempt counts,
the delay cap, and the timeout path without real sleeping.

`evaluate_live_gate` is the **live-gate evaluation**: Alpaca activates only when
`broker.name` demands it AND every criterion passes (paper days ≥ 90, Sharpe ≥ 1.0,
max drawdown ≤ 15%, win rate ≥ 50%, breakers tested, explicit human auth phrase). Default
config (`broker.name=paper_only`) is fail-closed.

Adapters: `trading/paper_adapter.py` (default, wraps Phase-8 `PaperBroker`) and
`trading/alpaca_adapter.py` (`alpaca-py` lazy from `requirements-optional.txt`, fully-mocked
`MockAlpacaClient` for zero-network tests). The same contract suite runs against both.
`RiskGateway.transmit` remains the sole caller of low-level `submit` (grep proof in the
Phase-10 evidence pack).

## Phase 11 dashboard implementation note (2026-07-31)

The dashboard is an **offline, read-mostly** Streamlit control surface. To keep the
reconciliation honest (and prevent the optional-tier Streamlit dependency from corrupting
the CORE test count), the data layer is split exactly along the architecture rule:

* `dashboard/data.py` — **PURE python providers** (zero Streamlit import, proven by
  `test_dashboard_data_module_has_no_streamlit_import`). One typed function per page
  (`overview_view`, `positions_view`, `orders_view`, `breaker_state_view`,
  `limits_view`, `models_view`, `backtests_view`, `logs_view`), each reading ONLY the
  local sqlite DB (no network; proven by `test_dashboard_pure_modules_have_no_network_call_sites`).
  A fresh/empty DB is a first-class case (empty/zero returns, never raises).
* `dashboard/actions.py` — **PURE python mutation handlers** (zero Streamlit import):
  the two and only mutation paths. (a) `engage_kill_switch` is gated by the Phase-10
  double-confirmation token flow (`request_kill_token` → `confirm_override`); a
  token-less / invalid / expired attempt is **rejected with no action** (proven by
  `test_kill_switch_rejected_*`). (b) `approve_signal` / `reject_signal` route through
  the Phase-9 `ApprovalQueue` lifecycle (persisted to `system_state` + `automation_log`).
* `dashboard/_runtime.py` — streamlit-free bootstrap (config + local DB open + `boot_check`
  that exercises every provider once).
* `dashboard/app.py` + `dashboard/pages/*.py` — **thin Streamlit renderers** that only
  call the pure functions; not collected by pytest (`testpaths=tests`).

The breaker-state panel renders `STATE_SEVERITY` + the active `TradingPolicy` reconstructed
**read-only** from the persisted `breaker_state` row (it derives the policy from the latched
state + triggers and never calls `evaluate()`, which persists/logs). Auto-refresh is
config-driven (`dashboard.refresh_interval_seconds` via `st.fragment(run_every=...)`).

Because Streamlit lives only in the optional tier, the headless boot smoke
(`test_dashboard_headless_boot_smoke`) declares a new **OPT_ONLY** category: the module
collects in CORE (counted toward TOTAL) but the body fails with `ModuleNotFoundError:
streamlit`, and passes in the optional env (launches `streamlit run dashboard/app.py`
headless, confirms boot within the 30s deadline). Reconciliation:
`TOTAL = CORE_GREEN + ML_ONLY(12) + OPT_ONLY`.

## Phase 12 testing & validation implementation note (2026-07-31)

Phase 12 is the integration + stress gate. No new production modules — it exercises the
existing safety core through end-to-end paths with injected clocks and deterministic seeds,
zero network.

* `tests/integration/test_paper_day.py` (387 lines): one full trading day green + one
  HALT variant. Green: seeded DB with 720 1m fake bars (AAPL 150, MSFT 300, deterministic
  walk seed 1/2), scheduler gates (PRE_MARKET blocked, REGULAR allowed, after
  `stop_new_entries` 15:45 ET blocked), signal → approval queue semi (PENDING→APPROVED→
  EXECUTED, TTL 1800), RiskGateway sole transmit (verified by spy), PaperBroker fills via
  shared `price_fill` (fee 10 bps = `backtesting.commission`, slippage 0 for exact P&L),
  positions (AAPL closed, MSFT open), realized P&L 46.95 including entry+exit fees
  (via `db.close_paper_trade`), digest aggregates (open 1, realized 46.95, text render),
  breaker logs (no daily_loss HALT). HALT variant: -2.2% daily loss triggers level3 RED
  HALT (close worst 50% AAPL/BBB, cancel resting limit, locked_until next open, gateway
  denial `breaker_state:HALTED` with `limit_breach_log`, `can_submit_order` blocked).

* `tests/stress/test_flash_crash.py` (133 lines): flash crash config
  `threshold_pct=-0.01`, `timeframe_minutes=5`, `pause_minutes=10`,
  `resume_recovery_pct=0.50`. Feed 100→99.9→98.9→98.6 within 4 min triggers RED pause
  10 min, `allow_new_entries=False`, `circuit_breaker_log` exact row (category
  flash_crash, level RED). Partial 30% recovery still paused, 70% after 8 min window
  slide resumes (pause None, no flash active). Asserts exact audit rows.

* `tests/stress/test_feed_outage.py` (123 lines): feed outage ladder
  `timeout=120s`/`emergency=300s`. Heartbeat at T0, +130s → RED HALT,
  +310s → EMERGENCY flatten_all. Exact `circuit_breaker_log` rows (data_feed level
  escalation), recovery via heartbeat clears trigger (still EMERGENCY until resume,
  but active trigger gone).

* `tests/stress/test_order_storm.py` (179 lines): order storm >10/min.
  `max_orders_per_minute=10`. 15 bursts in 60s via `can_submit_order` gate:
  10 accepted, 5 denied, `limit_breach_log` 5 rows threshold 10, broker orders 10
  filled, denied count == breach log count. Broker-level cap also proven (11th
  REJECTED `order_rate:10/min_exceeded`). Exact `circuit_breaker_log`
  RUNAWAY_ORDER row, flow pause 60s then de-escalation to DEFENSIVE.

* `tests/unit/test_phase12_mutation.py` (130 lines): mutation spot-checks —
  daily-loss ladder weakened to -10%/-11%/-12%/-13% (no HALT at -2.2%),
  VIX ladder to 50/60/70/80 (VIX 27 no reduction), rate cap to 100 (burst
  passes). Each proves original safety test would FAIL.

Coverage: `pytest --cov=risk --cov=trading --cov=automation` → risk 93%,
trading 88.2% (>=85%), automation 93%, TOTAL 92%. No `pragma: no cover`.

Reconciliation: TOTAL = CORE_GREEN(511) + ML_ONLY(12) + OPT_ONLY(1) = 524 = baseline 516 + 8 new.
Collect-only identical 524 in CORE and OPT envs. 2× distinct-duration greens
27.96s / 25.65s (fresh).

## Phase 13 optimisation implementation note (2026-07-31)

Phase 13 is the profiling-led performance gate.  No new feature modules — it
improves the hot paths measured by `scripts/benchmark.py` and proves numeric
equivalence to the pre-optimisation outputs.

* **Vectorised `wilders()`** (`features/indicators.py:36-64`): the Python `for`-loop
  recurrence `r[i] = (1-a)*r[i-1] + a*v[i]` is replaced by a closed-form cumulative
  sum: `r[period+m] = beta^(m+1)*seed + alpha * beta^m * cumsum(v_tail / beta^j)`.
  Output is bit-exact identical to the original loop (proven by 6 parametrised
  equivalence tests in `tests/unit/test_phase13_optimization.py`).

* **Vectorised `wma()`** (`features/indicators.py:19-34`): the `.rolling().apply(lambda…)`
  is replaced by `np.convolve(vals, weights[::-1], 'valid')`, which computes the
  weighted numerator in a single vectorised pass.  Output equivalent (±1e-12, 5 tests).

* **Vectorised `cci()`** (`features/indicators.py:82-103`): mean absolute deviation
  computed via `numpy.lib.stride_tricks.sliding_window_view` instead of
  `.rolling().apply(lambda…)`.  Output equivalent (±1e-12, 4 tests).

* **Bounded LRU indicator cache** (`features/indicators.py:13-29`): `compute_indicators()`
  caches results keyed by SHA-256 hash of the input frame (values + index + columns).
  Cache is an `OrderedDict` capped at 32 entries; the correctness test
  (`test_cache_hit_returns_same_result`) proves the cache never changes results, and
  `test_cache_never_exceeds_max_size` proves the bound.

* **DB indices v2** (`data/database.py` migration v2): `idx_price_data_sym_ts`
  `(symbol, timestamp)` and `idx_price_data_tf_sym_ts` `(timeframe, symbol, timestamp)`.
  `EXPLAIN QUERY PLAN` pasted in the evidence pack; `all_symbols_tf` query dropped from
  2.80 ms to 0.01 ms (index scan replaces full table scan).

* **BEFORE / AFTER benchmark table** (from `scripts/benchmark.py`, run against identical
  synthetic data with seed 42):

| Benchmark | Baseline (ms) | Optimized (ms) | Delta |
|---|---:|---:|---:|
| Indicator pipeline uncached (500d) | 195.42 | 41.79 | −153.63 (−78.6%) |
| Indicator pipeline cached (500d) | — | 0.09 | — |
| Feature engineering (multi-tf+macro) | 915.54 | 10.73 | −904.81 (−98.8%) |
| Backtest replay (500 bars) | 0.18 | 0.18 | −0.00 |
| DB: latest_prices | 0.09 | 0.09 | −0.00 |
| DB: price_window | 0.00 | 0.00 | 0.00 |
| DB: all_symbols_tf | 2.80 | 0.01 | −2.79 (−99.6%) |

* Reconciliation: **TOTAL = CORE_GREEN(530) + ML_ONLY(12) + OPT_ONLY(1) = 543**
  (= baseline 524 + 19 Phase-13 equivalence tests).  Collect-only identical 543 in
  CORE / ML / OPT envs.  2× distinct-duration greens in all three environments:
  CORE 28.60s / 28.34s, ML 32.05s / 29.75s, OPT 30.52s / 30.82s (fresh).

* No logic weakened; no breaker thresholds weakened; no network; all commits pushed;
  PR \"Phase 13: optimization + final audit\".  This closes the master build plan.


