# BUILD CONTINUATION PROMPT — `fin-trade` (Phases 2 → 13)

> **How to use:** Paste this ENTIRE prompt into your open build session (or as the first message
> of a new session for repo `surendra-ksr/fin-trade`). The agent must execute every step in
> order, autonomously — **never ask for permission** between phases. If remote GitHub access
> fails at any point, say so immediately and stop.
>
> **Priorities: Correctness > Safety > Performance.** Paper-trading-only default. Circuit
> breakers and the kill switch can never be disabled. No look-ahead bias; walk-forward
> validation; purged + embargoed CV. Full production code — zero stubs/placeholders —
> with docstrings, type hints, logging, and behavioral unit tests for every module (PEP 8).
> The suite must be 100% green, run TWICE with identical counts, before every PR.

---

## AUTHORITATIVE SOURCES (read first, in this order)

1. `docs/AUDIT_REPORT.md` (merged via PR #2) — **the gap list**. Every ❌/⚠️ in it is a work item.
2. `docs/ARCHITECTURE.md` — design decisions, roadmap, §9 seven-item self-check.
3. `config.yaml` — spec thresholds (do not weaken any of them).
4. This prompt's phase map (below) — acceptance criteria; where sources disagree, choose the
   SAFER option and record the deviation in `docs/AUDIT_REPORT.md`.

---

## STEP 0 — Ground truth in-repo

Create `docs/BUILD_PLAN.md` containing the 13-phase acceptance map from this prompt (verbatim),
linked from `docs/ARCHITECTURE.md`. All future sessions must treat it as the plan of record.

## STEP 1 — Requirements repair (own commit, may ship inside the Phase-2 PR)

`pip install -r requirements.txt` on **Python 3.11** MUST succeed end-to-end. Known break from
the audit: `scipy==1.18.0`. pip stops at the first failure — so verify EVERY pin resolves on
3.11 (torch, tensorflow, scikit-learn, streamlit, alpaca-py, etc.). Fix by pinning the newest
release that supports 3.11 (or `>=,<` bounds / environment markers), with a comment stating why.
Prove with a clean-surface install, then run the full test suite.

## STEP 2 — Phase 2: Data & Features (PR: "Phase 2: data & features")

Close every Phase-2 gap in the audit report, plus:
- `features/indicators.py`: full pure-pandas/numpy suite (NO TA-Lib) — trend (SMA/EMA/WMA, MACD,
  ADX/DMI, Parabolic SAR, Ichimoku), momentum (RSI, Stochastic, CCI, ROC, Williams %R, MFI),
  volatility (Bollinger, ATR, Keltner, Donchian, rolling std), volume (OBV, VWAP, A/D, CMF,
  volume z-score). Each validated against hand-computed vectors.
- `features/feature_engineer.py`: multi-timeframe + derived (returns, lags, rolling stats) +
  intermarket features (SPY/QQQ beta & correlation, VIX regime, macro joins). Features at time t
  use only data ≤ t — include explicit anti-look-ahead tests (`grep "\.shift(-"` must be empty).
- `data/quality.py`: duplicates, gaps, OHLC sanity, outlier z-score, stale data, corporate-action
  jump flags.
- `agents/data_agent.py` extensions: Alpha Vantage fundamentals (only when `ALPHAVANTAGE_API_KEY`
  set; graceful skip otherwise), SEC EDGAR company facts, options IV surface (yfinance), news.

## STEPS 3–13 — Remaining phases (one phase at a time; PR label "Phase N: …")

For each: implement fully → behavioral unit tests → update `docs/ARCHITECTURE.md` roadmap →
suite 100% green twice → commit/push the session branch (PR per the session/branch cadence —
if the session closes on merge, the user's next session continues from this map).

- **Phase 3 — Core models:** `models/base.py` ABC (fit/predict/save/load) + versioned DB registry;
  LSTM, GRU, tree ensemble (XGBoost/LightGBM or sklearn); trainer with walk-forward splits and
  purged + embargoed K-fold CV (embargo > 0); sequence builder provably past-only; metrics registry.
- **Phase 4 — Advanced models:** Transformer/TFT/N-HiTS or stacked ensemble; regime detector
  (HMM or rule-based on VIX/trend/vol); Optuna search nested INSIDE walk-forward (no test leakage);
  probability calibration (Platt/isotonic).
- **Phase 5 — Sentiment + patterns:** news sentiment (FinBERT, lexicon fallback); candlestick
  engine writing `patterns_detected` with self-labeled `outcome_5d/10d/20d`; chart patterns.
- **Phase 6 — Backtesting:** event-driven engine (optional vectorized fast path); spread/slippage/
  commission; NEXT-bar fills only (explicit anti-look-ahead test: every fill ts > signal ts);
  walk-forward runner; purged CV utility; reports (equity, DD, Sharpe/Sortino/Calmar, hit rate,
  exposure) → gitignored `backtesting/reports/`.
- **Phase 7 — Risk limits & gateway:** per-asset / per-strategy / per-sector / portfolio gross+net
  exposure limits; speed breakers (daily, weekly/monthly, per-strategy, per-asset, drawdown);
  `RiskGateway` that EVERY order (manual/auto/paper/live) passes BEFORE any broker transmission;
  breaches → `limit_breach_log`.
- **Phase 8 — Orders + paper trading:** market/limit/stop/stop-limit/trailing/OCO/bracket;
  `trading/paper_broker.py`: realistic fills (next-bar open or modeled intrabar slippage), fees,
  realized P&L INCLUDING entry fees, idempotent submission (30s duplicate window, 10 orders/min).
- **Phase 9 — Automation:** ET market-hours scheduler with pre/post-market guards;
  `semi_automated` human-approval queue; `fully_automated` honors config; recovery manager
  (25%/50%/75%/100% size ramp, `cooling_off_days: 5`); daily digest; startup reconciliation.
- **Phase 10 — Broker integration:** `trading/broker_base.py` ABC; retry w/ exponential backoff +
  timeout on EVERY call; risk-gateway check BEFORE transmission; adapters: paper (default) +
  Alpaca (`alpaca-py`, behind `broker.name` AND the full live gate: ≥90d paper, Sharpe ≥ 1.0,
  max DD ≤ 15%, win rate ≥ 50%, all breakers tested, explicit human authorization); kill switch
  wired through the adapter (cancel-all + flatten + token-confirmed human resume).
- **Phase 11 — Dashboard:** Streamlit multi-page (overview, positions, orders, breaker state,
  limits, models, backtests, logs); kill-switch button w/ token confirm; auto-refresh; read-mostly;
  runs offline against the local DB; headless 30s boot smoke test.
- **Phase 12 — Testing & validation:** `tests/integration/` (end-to-end simulated paper day);
  `tests/stress/` (flash-crash sim, feed outage, order storm verifying 10/min cap); mutation
  spot-checks on thresholds; coverage ≥ 85% on `risk/` and `trading/`.
- **Phase 13 — Optimization:** profiling-driven vectorization of hot paths; indicator/feature
  caching; DB indices on hot queries; benchmark script + results documented.

## FINAL STEP — Re-audit

Re-run the full master audit (stub sweep, functional breaker scenarios live, anti-look-ahead grep,
determinism ×2, safety invariants, coverage gates). Update `docs/AUDIT_REPORT.md` and re-answer the
§9 seven-item checklist with file:line references. Final PR: "Final validation: audit + report".

## HARD RULES (never violated)

- Never weaken safety thresholds or bypass the breaker manager / risk gateway — for anyone, ever.
- Secrets via environment only (`.env` ignored; `.env.example` documents every var).
- Keep artifacts out of git: `data/live/`, `logs/`, `backtesting/reports/`, trained model binaries.
- No stubs, no vacuous tests, no network-dependent unit tests, fixed seeds, injected clocks.
