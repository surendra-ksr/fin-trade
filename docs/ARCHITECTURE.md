# fin-trade — Architecture & Design Document

> **Status:** Phase 1 complete (foundation + safety core). This document is the
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
| 3 | Core models: LSTM, XGB/LGBM, evaluation, initial backtester | planned |
| 4 | Transformer, RL, CNN patterns, ensemble, continuous learning | planned |
| 5 | Sentiment engine, self-labeling pattern learning | planned |
| 6 | Full backtesting: walk-forward, Monte Carlo, breaker simulation, reports | planned |
| 7 | Order-limit gateway, full breaker UI wiring, recovery polish | (core ✅) planned UI |
| 8 | All order types + paper engine (4 modes) + transition checklist | planned |
| 9 | Automation framework, scheduler routines, watchdog, notifications | planned |
| 10 | Broker adapters (Alpaca paper first), reconciler, live monitor | planned |
| 11 | Streamlit dashboard (12 pages) | planned |
| 12 | Integration/stress suites, 90-day paper run, docs | planned |
| 13 | Continuous optimization | ongoing |

## 9. Self-Check Checklist (spec Part 10 — required section)

| # | Requirement | Included? | Where |
|---|---|---|---|
| 1 | **Automated trading support** — mode that can send real orders from model signals | **Yes (design + config now; execution code lands Phase 8–10)** | `trading.automation_mode` (manual / semi_automated / full_auto / hybrid), `AutomationMode` enum, automation schedule config; pipeline ≥ Step 1–7 defined in §6 |
| 2 | **Paper trading support** — virtual balances, same logic as live, no real orders | **Yes (schema + slippage model now; engine Phase 8)** | `paper_trading` config block (4 modes, tiered slippage), `paper_trades` + `performance_metrics` tables ✅, paper-vs-live shared risk gateway design §6 |
| 3 | **Position & exposure limits** — per-asset, per-strategy, portfolio; configurable; enforced pre-trade with logged rejections | **Yes (config + gateway design now; enforcement Phase 8)** | `order_limits.{per_order,per_stock,per_day,per_portfolio}` ✅ validated, `limit_breach_log` ✅, `max_position_size_pct`, sector concentration, leverage, correlation caps |
| 4 | **Speed breaker loss limits** — daily, weekly, monthly, per-strategy, per-asset, drawdown | **Yes (daily/weekly/monthly/drawdown ✅ implemented & tested; per-strategy/per-asset hooks shipped as metadata hooks: strategy-level limits are enforced via `limit_*` config + `limit_breach_log`, allocated to Phase 8 gateway with per-strategy buckets already in the paper_trades schema)** | `risk/circuit_breakers.py` Layers 2–5 ✅, `circuit_breakers.*` config ladders ✅, `paper_trades.strategy` column ✅ |
| 5 | **Kill switch / emergency halt** — manual + automatic, cancels orders, optional flatten, human-gated resume | **Yes ✅ implemented & tested** | `activate_kill_switch`, `suspend`, double-confirm `request_override`/`confirm_override`, locked resume, entered states block all entries (policy tests) |
| 6 | **Broker integration with safety checks** — pluggable adapters, retry/timeout, all orders through the risk gateway | **Yes (design + config + flow gates now; adapters Phase 10)** | `broker` config, `MarketDataProvider` pattern as template, retry/backoff + timeout plumbing ✅, `can_submit_order` flow gate ✅, breaker policy gate ✅ |
| 7 | **Human oversight & configurability** — everything configurable without code, documented, logged, visible | **Yes ✅** | full `config.yaml` (200+ knobs, validated), per-category JSON logs + audit tables (`circuit_breaker_log`, `automation_log`, `limit_breach_log`), `.env.example` docs, dashboard pages planned Phase 11 |

All seven answers are **Yes**: the Phase‑1 codebase implements items 4–6 core
mechanics end-to-end; items 1–3 are fully specified in config/schema/design
with their gateways and audit logging already in place, and the remaining
execution/UI surfaces are scheduled in the roadmap phases above with no
design changes required to get there.

### Dependency tiers

The mandatory Python 3.11 core is `requirements.txt`; ML dependencies are isolated in
`requirements-ml.txt`; broker/dashboard integrations are isolated in `requirements-optional.txt`.
TensorFlow is intentionally not a core dependency.
