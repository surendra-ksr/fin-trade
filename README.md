# fin-trade — Local AI Stock-Trading Research & Execution Agent

> **Research & education software — not financial advice.**  Defaults are
> paper-trading only; real-money trading is gated behind a 90-day paper
> validation checklist, multiple explicit authorizations, and always-on
> circuit breakers.  **All 13 phases of the master build plan are complete**
> (2026-07-31).

A local-first, self-learning, automated stock-trading system with:

- **Configurable pipelines** for OHLCV (1m→1mo), fundamentals, macro (FRED),
  news/sentiment, and options put/call metrics — incrementally synced into
  local SQLite (WAL) with quality checks and timezone-normalized UTC data.
- **A 7-layer circuit-breaker / speed-breaker safety core**: daily/weekly/
  monthly loss speed breakers, peak-drawdown breakers with cooling-off locks,
  VIX/market/flash-crash/sector/liquidity breakers, technical-failure
  watchdogs, kill switch, suspend, override tokens, and a graduated recovery
  program.  All state persisted & audited.
- **Full ML pipeline**: LSTM/GRU/tree-ensemble models, stacked meta-learner,
  regime detector, nested Optuna optimisation (no leakage), probability
  calibration, FinBERT sentiment with lexicon fallback, candlestick pattern
  engine with self-labeling outcomes.
- **Event-driven backtesting** with next-bar fills, realistic costs, purged
  walk-forward CV, and anti-look-ahead tests.
- **RiskGateway** — every order must pass through it.  Per-asset, per-strategy,
  per-sector, and portfolio limits; breaches logged to `limit_breach_log`.
- **Paper trading** with realistic fills (shared `price_fill` core), FIFO
  positions, P&L including fees, idempotency caps (30s duplicate window,
  10 orders/min).  All 7 order types (market/limit/stop/stop-limit/trailing/
  OCO/bracket) with validated state machine.
- **Automation**: US market-hours scheduler (DST + holiday aware, injected
  clock), semi-automated approval queue (TTL + persistence), graduated
  recovery ramp (25/50/75/100% + cooling-off), daily digest, startup
  reconciliation.
- **Broker integration**: unified ABC with retry/timeout, paper + gated
  Alpaca adapters, live-gate evaluation (≥90d / Sharpe≥1 / maxDD≤15% /
  WR≥50% / breakers tested / human auth), kill-switch wired through adapters.
- **8-page read-mostly Streamlit dashboard**: overview, positions, orders,
  breaker state, limits, models, backtests, logs; token-confirmed kill switch
  + approval queue mutations.
- **Integration + stress tested**: full paper trading day (green + HALT
  variant), flash-crash pause/resume, feed-outage ladder, order-storm cap
  with breach-log matching, mutation spot-checks on safety thresholds.
- **Everything-as-config**: 200+ knobs in `config.yaml` (validated), secrets
  in `.env`, structured per-category JSON logs.
- **Phase 13 optimised**: vectorised hot paths (wilders/wma/cci), bounded LRU
  indicator cache, DB indices with EXPLAIN QUERY PLAN proof; 543-test suite.

## Project status — ALL PHASES COMPLETE

| Phase | Scope | Status |
|---|---|---|
| **1** | Foundation: config, logging, utils, SQLite, DataAgent, CircuitBreakers | ✅ |
| **2** | Data & features: 100+ indicators, multi-timeframe, intermarket, macro, quality | ✅ |
| **3** | Core models: LSTM, GRU, GBM, walk-forward purged CV, metrics | ✅ |
| **4** | Advanced models: stacked ensemble, regime detector, nested Optuna, calibration | ✅ |
| **5** | Sentiment + patterns: FinBERT/lexicon, candlestick engine, self-labeling | ✅ |
| **6** | Backtesting: event-driven, next-bar fills, costs, purged CV, anti-look-ahead | ✅ |
| **7** | Risk limits & gateway: per-asset/strategy/sector/portfolio, speed breakers | ✅ |
| **8** | Order types + paper trading: 7 types, realistic fills, fees, idempotency | ✅ |
| **9** | Automation: scheduler, approval queue, recovery ramp, digest, reconcile | ✅ |
| **10** | Broker integration: ABC, retry/timeout, paper + gated Alpaca, kill switch | ✅ |
| **11** | Dashboard: 8 Streamlit pages, offline, read-mostly, token-gated mutations | ✅ |
| **12** | Testing & validation: integration paper day, stress scenarios, mutation, coverage ≥85% | ✅ |
| **13** | Optimization: vectorised hot paths, indicator cache, DB indices, benchmarks | ✅ |

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design,
including the mandatory **self-check checklist** (all 7 items **Yes** with
file:line references).

See [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md) for the per-phase acceptance
evidence trail.

## Quickstart

```bash
# 1) prerequisites: Python 3.11+
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2) configure (optional for paper-only research)
cp .env.example .env

# 3) run the full test suite (no network needed)
.venv/bin/python -m pytest tests/ -q     # 530 passed in CORE, 543 total

# 4) bootstrap data (uses config.yaml watchlist)
python - <<'PY'
from utils.config import load_config
from data.database import DatabaseManager
from agents.data_agent import DataAgent

cfg = load_config("config.yaml")
db = DatabaseManager(cfg.resolve_path(cfg.data.database_path))
agent = DataAgent(cfg, db)
agent.sync_all(timeframes=["1d"], include_fundamentals=True)
agent.sync_macro()
print(db.table_stats())
PY
```

## Safety guarantees

- **Default mode: paper trading.** `config.yaml` `broker.name: paper_only`.
  Live trading requires an explicit multi-criteria gate including ≥90 days of
  paper history, Sharpe ≥1.0, max drawdown ≤15%, win rate ≥50%, all breakers
  tested, and a human authorization phrase — enforced in code, not docs.
- **Circuit breakers cannot be silently disabled.** Disabling requires an
  explicit config change (`circuit_breakers.enabled: false`) which is
  clearly documented as unsafe; the default is `true`.
- **Every order passes through `RiskGateway.transmit`** at
  `risk/position_limits.py:153` — this is the one and only call to
  `broker.submit()` in the entire codebase (grep-proven).
- **Kill switch = cancel-all + flatten + human-gated (token-confirmed) resume.**
- **Secrets only via environment.** `.env` is gitignored and has never been
  committed; `.env.example` documents every variable.
- **No look-ahead anywhere.** Zero hits for `.shift(-` outside docs (grep-proven).
  Walk-forward folds use purged/embargoed splits; sequence builder uses only
  past windows; feature pipelines use backward-only merge_asof.

## Repository layout

```
utils/      constants (enums) · loguru logging · config loader/validation · helpers
data/       SQLite manager, 16-table schema, migrations v2, CRUD, backup, audit logs
agents/     DataAgent (universe · OHLCV · fundamentals · macro · options · quality)
features/   indicators.py (optimised) · feature_engineer.py (causal joins)
models/     base (ABC/registry) · neural (LSTM/GRU) · gbm_baseline · ensemble
            regime_detector · optimization (nested Optuna) · calibration
            sentiment (FinBERT + lexicon) · patterns (candlestick + self-labeling)
backtest/   engine · fill_engine (shared price_fill) · order_engine · reports
risk/       circuit_breakers.py (7 layers) · position_limits.py (RiskGateway)
trading/    order_types (7 types + state machine) · paper_broker · broker_base
            paper_adapter · alpaca_adapter · core (compat re-exports)
automation/ scheduler · approval_queue · recovery · digest · reconcile
dashboard/  data.py (pure providers) · actions.py (mutations) · app.py + pages/
scripts/    benchmark.py (Phase 13 BEFORE/AFTER harness)
tests/      unit/ (30 files) · integration/ · stress/
docs/       ARCHITECTURE.md · BUILD_PLAN.md · AUDIT_REPORT.md · EVIDENCE_PROTOCOL.md
            PHASE*.md evidence packs
config.yaml master configuration (200+ validated knobs)
.env.example secrets template (12 vars documented)
requirements.txt · requirements-ml.txt · requirements-optional.txt
```

## Dependency tiers

| Tier | File | What it covers | Install |
|---|---|---|---|
| **CORE** | `requirements.txt` | Phases 1–2, 6–9, 12–13: numpy, pandas, scipy, PyYAML, loguru, yfinance, pytest | `pip install -r requirements.txt` |
| **ML** | `requirements-ml.txt` | Phases 3–5: torch, scikit-learn, optuna, shap, transformers, stable-baselines3 | `pip install -r requirements-ml.txt` (after CORE) |
| **OPTIONAL** | `requirements-optional.txt` | Phases 10–11: streamlit, plotly, alpaca-py | `pip install -r requirements-optional.txt` (after CORE+ML) |

All tiers validated on Python 3.11 with 2× green full-suite runs (2026-07-31).
TensorFlow is intentionally excluded — Torch-first ML stack.
