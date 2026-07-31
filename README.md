# fin-trade — Local AI Stock-Trading Research & Execution Agent

> Research & education software — **not financial advice**. Defaults are
> paper-trading only; real-money trading is gated behind a 90-day paper
> validation checklist, multiple explicit authorizations, and always-on
> circuit breakers.

A local-first, self-learning, automated stock-trading system with:

- **Configurable pipelines** for OHLCV (1m→1mo), fundamentals, macro (FRED),
  news/sentiment, and options put/call metrics — incrementally synced into
  local SQLite (WAL) with quality checks and timezone-normalized UTC data.
- **A 7-layer circuit-breaker / speed-breaker safety core** (implemented and
  fully tested): daily/weekly/monthly loss speed breakers, peak-drawdown
  breakers with cooling-off locks, VIX/market/flash-crash/sector/liquidity
  breakers, technical-failure watchdogs, kill switch, suspend, override
  tokens, and a graduated recovery program. All state persisted & audited.
- **Everything-as-config**: 200+ knobs in `config.yaml` (validated), secrets
  in `.env`, structured per-category JSON logs.

## Project status

| Phase | Scope | Status |
|---|---|---|
| **1** | Foundation: config, logging, utils, SQLite schema+CRUD, DataAgent, **CircuitBreakers** | ✅ **complete — 233 unit tests green** |
| 2 | Full data coverage & feature engineering (100+ indicators) | next |
| 3–6 | Models (LSTM/Transformer/XGB/RL/ensemble), sentiment, patterns, backtesting | planned |
| 7–10 | Order types, paper engine (4 modes), automation, broker adapters | planned |
| 11–13 | Dashboard (12 Streamlit pages), integration runs, optimization | planned |

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design,
including the mandatory **self-check checklist** (automated trading, paper
trading, limits, speed breakers, kill switch, broker safety, oversight).

## Quickstart

```bash
# 1) prerequisites: Python 3.11+
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # core + phase-tagged optional deps

# 2) configure (optional for paper-only research)
cp .env.example .env                      # fill what you have; empty is fine

# 3) run the test suite (no network needed, faked providers)
python3 -m pytest tests/unit -q           # 233 passed

# 4) bootstrap data (uses config.yaml watchlist; yfinance needs no key)
python - <<'PY'
from utils.config import load_config
from data.database import DatabaseManager
from agents.data_agent import DataAgent

cfg = load_config("config.yaml")
db = DatabaseManager(cfg.resolve_path(cfg.data.database_path))
agent = DataAgent(cfg, db)
agent.sync_all(timeframes=["1d"], include_fundamentals=True)
agent.sync_macro()                        # FRED works keyless (public CSV)
print(db.table_stats())
PY
```

## Safety model (tl;dr)

`NORMAL < CAUTION < RESTRICTED < DEFENSIVE < HALTED < EMERGENCY < SUSPENDED`

Every evaluation produces a single authoritative **TradingPolicy**: size
multiplier, confidence gate, entry permissions, forced actions (close worst
50%, exit sector, flatten), lock timers, and required operator actions.
Sticky halts survive restarts; loss-based halts need a token-confirmed human
resume; condition-driven halts (VIX, feeds, flash crash) auto-recover.

Examples of what the breakers do out of the box:

- Daily loss: −1% warn → −1.5% block entries/cancel pendings → −2% halt +
  close worst half → −3% flatten everything and lock two sessions.
- Drawdown from peak: −5% trim → −15% EMERGENCY + 5-day cooling-off +
  forced backtest review before any re-authorization.
- Kill switch: one call (`activate_kill_switch`) cancels flow, flattens, and
  requires explicit double-confirmation to resume.

## Repository layout (grows per phase)

```
utils/      constants (enums) · loguru logging · config loader/validation · helpers
data/       SQLite manager, 16-table schema, migrations, CRUD, backup, audit logs
agents/     DataAgent (universe · OHLCV · fundamentals · macro · options · quality)
risk/       CircuitBreakerManager — the safety core
tests/      unit/ (233 tests) · integration/ · stress/
docs/       ARCHITECTURE.md (design + roadmap + self-check)
config.yaml master configuration   ·   .env.example secrets template
requirements.txt pinned deps, phase-tagged   ·   pytest.ini · conftest.py
```

## Dependency tiers

Core Phases 1–2 dependencies are pinned in `requirements.txt` and are validated on
Python 3.11. Phase 3–5 ML dependencies are isolated in `requirements-ml.txt` (Torch-first;
TensorFlow is intentionally excluded). Dashboard and broker integrations are in
`requirements-optional.txt` and should only be installed for their phase.
