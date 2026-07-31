# MASTER AUDIT & VERIFICATION PROMPT — Local AI Stock Trading Agent (`fin-trade`)

> **How to use:** Paste this ENTIRE prompt as the first message of a new coding session for the
> repo `surendra-ksr/fin-trade`. The agent must execute every section in order, autonomously —
> do NOT ask for permission at any step. Finish with: all findings fixed, the full test suite
> 100% green, and a pull request opened with the audit report + fixes.
> Priorities: **Correctness > Safety > Performance.** If this session has no GitHub remote
> access, say so immediately and stop.

---

## 1. MISSION

Perform a full, skeptical audit of the repository against the master plan. Prove **with commands,
code reading, and live runs — never assumptions** — that:

1. **Completeness:** every planned item is implemented (present *and* containing real logic).
   Nothing missed, nothing silently skipped.
2. **No fake implementations:** no empty classes/functions, stub bodies (`pass`, `...`),
   placeholder returns, TODO-driven gaps, dead modules never imported, or docstring-only files.
   A file that *exists* is NOT evidence it is *implemented* — read the code.
3. **Everything works:** dependencies install, the full test suite passes 100% (twice, for
   determinism), and every subsystem is exercised by a live functional smoke scenario — not
   just unit tests.
4. **Safety invariants hold:** paper-trading-only default, circuit breakers can never be
   disabled, kill switch works (cancel-all + flatten + human-gated resume), live trading is
   hard-gated.
5. Produce a written audit report （报告 per §10) mapping every finding to its plan location.

---

## 2. GROUND TRUTH — WHERE THE PLAN LIVES

| Artifact | Location in repo | What it pins down |
|---|---|---|
| Architecture & roadmap | `docs/ARCHITECTURE.md` | Design decisions, per-phase roadmap, no-look-ahead / walk-forward / purged-CV rules, and the §9 seven-item self-check checklist (all must be "Yes" with code references) |
| Spec values | `config.yaml` | All thresholds from the master spec, embedded verbatim (verify against §6.1 below) |
| Behavior pins | `tests/unit/*.py` | Executable specification of Phase-1 behavior (233 tests at Phase-1 baseline; must only grow) |
| Quickstart & structure | `README.md` | Intended layout and run instructions |
| This prompt | §5 phase map | Acceptance criteria per phase — use when repo docs drift or are vague |

**Rule:** if repo docs and this prompt disagree, implement to the SAFER option, and record the
deviation in the audit report.

---

## 3. ENVIRONMENT BASELINE (run first, fix before continuing)

```bash
python3 --version                     # must be >= 3.11
pip install --user --break-system-packages -r requirements.txt   # or the minimal set that makes tests run
cd <repo> && python3 -m pytest tests/unit -q
python3 -m pytest tests/unit -q       # run a SECOND time — results must be identical (determinism)
```

Acceptance: 100% pass, 0 unexpected skips, 0 xfail without a linked reason, warnings rationalized.
Anything failing → fix FIRST, then continue the audit.

---

## 4. STUB / FAKE-IMPLEMENTATION SWEEP (exact commands, then judgment)

```bash
# 4a. Suspicious markers
grep -RInE "TODO|FIXME|XXX|HACK|placeholder|stub|not implemented|NotImplementedError|coming soon|wire.?up later" --include="*.py" .

# 4b. Suspiciously small files (allowed empty: __init__.py, .gitkeep)
find . -name "*.py" -not -path "./.git/*" -exec wc -l {} + | sort -n | head -40

# 4c. AST check — functions/classes whose entire body is pass / ... / docstring-only
python3 - <<'PY'
import ast, pathlib
for p in pathlib.Path('.').rglob('*.py'):
    if '.git' in p.parts: continue
    tree = ast.parse(p.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = [s for s in node.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str))]
            if not body or all(isinstance(s, ast.Pass) or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and s.value.value is Ellipsis) for s in body):
                print(f"{p}:{node.lineno} EMPTY FUNCTION {node.name}")
        if isinstance(node, ast.ClassDef):
            real = [s for s in node.body if not isinstance(s, ast.Pass)]
            if not real:
                print(f"{p}:{node.lineno} EMPTY CLASS {node.name}")
PY

# 4d. Modules never imported anywhere (dead code) — manual review
grep -RIn "^import \|^from " --include="*.py" . | sort

# 4e. Gitignore hygiene
git check-ignore -v .env logs/x.log data/live/x.csv backtesting/reports/x.html 2>&1   # all must be ignored
ls .env 2>&1   # must NOT exist / never committed (git log --all -- .env  -> empty)
```

**Judgment rules:** abstract methods raising `NotImplementedError` in a *documented* ABC are OK;
pytest skips with a reason are OK; intentional ellipses in type stubs are OK. Everything else must
be **implemented for real**, not excused. List every hit + verdict (OK / FIXED) in the report.

---

## 5. MASTER PHASE MAP — ACCEPTANCE CRITERIA PER PHASE

For each phase: verify every listed deliverable exists, is *implemented* (open the file, confirm
real logic — sized plausibly, returns real values, wired into callers), has unit tests that assert
behavior, and is referenced from `docs/ARCHITECTURE.md`'s roadmap
(**plan location: docs/ARCHITECTURE.md §Roadmap phase N**, plus the spec values in `config.yaml`).

### Phase 1 — Foundation + Safety Core  ✅ merged (verify fully anyway)
Files: `requirements.txt`, `.env.example`, `config.yaml`, `utils/constants.py`, `utils/logger.py`,
`utils/config.py`, `utils/helpers.py`, `data/database.py`, `agents/data_agent.py`,
`risk/circuit_breakers.py`, `conftest.py`, `pytest.ini`, `.gitignore`, `docs/ARCHITECTURE.md`,
`tests/unit/` (7 files, 233 tests baseline).
- States NORMAL<CAUTION<RESTRICTED<DEFENSIVE<HALTED<EMERGENCY<SUSPENDED with int severities and a
  VALID_STATE_TRANSITIONS map (escalation always allowed; de-escalation one step per cycle;
  SUSPENDED → HALTED only).
- Sticky triggers (loss halts, kill switch, data mismatch) latch and need human resume; condition
  triggers (VIX / crash / feed / flash) auto-clear. HALTED auto-recovers ONLY if purely
  condition-driven. EMERGENCY/SUSPENDED always need token-confirmed human resume.
- TradingPolicy = single authoritative object (size multiplier, confidence boost, allow_new_entries/
  longs/shorts, flatten_all, cancel_pending_orders, tighten_stops, required_actions, blocked lists,
  reasons). Aggregation = most-conservative-wins (min multiplier, max boost).
- DB: stdlib sqlite3, WAL, thread-local connections + writer lock, 16 tables, versioned migrations,
  `breaker_state` single-row persistence (id=1), audit tables `circuit_breaker_log`,
  `automation_log`, `limit_breach_log`, `patterns_detected` with `outcome_5d/10d/20d` columns.
- Data agent: yfinance lookback caps enforced (1m→7d; 5m/15m/30m→60d; 1h→730d; 4h resampled locally
  from 1h, anchored to session start); fundamentals; macro via FRED (official API with key, else
  keyless `fredgraph.csv`); S&P 500 / Nasdaq-100 universe via stdlib HTMLParser + 24h cache + static
  fallback; symbols normalized `.`→`-` (BRK.B → BRK-B).
- requirements.txt: TA-Lib / pyfolio / robin-stocks excluded with documented reasons.

### Phase 2 — Data & Features
- `features/indicators.py` (or equivalent): full suite, pure pandas/numpy (NO TA-Lib):
  trend (SMA/EMA/WMA, MACD, ADX/DMI, Parabolic SAR, Ichimoku), momentum (RSI, Stochastic, CCI,
  ROC, Williams %R, MFI), volatility (Bollinger, ATR, Keltner, Donchian, rolling std),
  volume (OBV, VWAP, A/D line, CMF, volume z-score). Each validated against hand-computed values.
- `features/feature_engineer.py`: multi-timeframe features, derived features (returns, lag,
  rolling stats), intermarket features (SPY/QQQ beta & correlation, VIX regime, macro joins).
  **No look-ahead:** features at time t use only data ≤ t (audit every `rolling`/`shift`).
- `data/quality.py`: duplicate/gap detection, OHLC sanity (H≥L, etc.), outlier z-score, stale-data
  detection, corporate-action jump flagging.
- `agents/data_agent.py` extensions: Alpha Vantage fundamentals (only when `ALPHAVANTAGE_API_KEY`
  present; graceful skip otherwise), SEC EDGAR company-facts fetch, options IV surface via yfinance,
  news ingestion.

### Phase 3 — Core Models
- `models/base.py` ABC: fit/predict/save/load + versioned registry persisted in DB.
- Models: LSTM, GRU, tree ensemble (XGBoost/LightGBM or sklearn RF/GBM baseline).
- Trainer with **walk-forward** splits and **purged, embargoed K-fold CV** (embargo > 0);
  sequence builder provably uses only past data; metrics registry (RMSE/MAE/directional accuracy).

### Phase 4 — Advanced Models
- At least one advanced architecture (Transformer/TFT/N-HiTS or stacked ensemble), market-regime
  detector (HMM or rule-based on VIX/trend/vol), Optuna hyperparameter search nested INSIDE the
  walk-forward loop (no test leakage), probability calibration (Platt/isotonic).

### Phase 5 — Sentiment + Patterns
- News sentiment (FinBERT if available, lexicon fallback), candlestick pattern engine writing to
  `patterns_detected` with self-labeling outcomes (`outcome_5d/10d/20d`), chart-pattern detection.

### Phase 6 — Backtesting
- Event-driven engine (+ optional vectorized fast path); realistic costs (spread/slippage/
  commission); fills on NEXT bar (no same-close fills) — include an explicit anti-look-ahead test;
  walk-forward runner; purged CV utility; reports (equity, drawdown, Sharpe/Sortino/Calmar, hit
  rate, exposure) written to gitignored `backtesting/reports/`.

### Phase 7 — Risk Limits & Gateway
- `risk/position_limits.py`: per-asset, per-strategy, per-sector, and portfolio gross/net exposure
  limits; **speed breakers**: daily + weekly/monthly + per-strategy + per-asset + drawdown (per the
  architecture spec); **RiskGateway**: EVERY order — manual, automated, paper, or live — must pass
  through it before any broker transmission; breaches logged to `limit_breach_log`.

### Phase 8 — Order Types + Paper Trading
- `trading/order_types.py`: market/limit/stop/stop-limit/trailing/OCO/bracket.
- `trading/paper_broker.py`: realistic fills (next-bar open or modeled intrabar slippage), fees,
  position tracking, realized P&L INCLUDING fees on close (uses `close_paper_trade`), idempotent
  submission honoring the 30s duplicate window and 10 orders/min cap.

### Phase 9 — Automation
- Scheduler bound to US market hours (ET) with pre/post-market guards; `semi_automated` mode
  requires human approval queue, `fully_automated` honors config; recovery manager implementing the
  25%/50%/75%/100% size ramp + `cooling_off_days: 5`; daily digest; startup reconciliation vs DB.

### Phase 10 — Broker Integration
- `trading/broker_base.py` ABC (submit/cancel/replace/positions/orders/account), retry with
  exponential backoff + timeout on EVERY call, risk-gateway check BEFORE transmission; adapters:
  paper (default) + Alpaca via `alpaca-py`, activated only by `broker.name` AND the full
  live-trading gate; kill switch wired through the adapter (cancel-all + flatten).

### Phase 11 — Dashboard
- Streamlit multi-page: overview, positions, orders, breaker state, limits, models, backtests,
  logs; manual kill-switch button with confirmation token; auto-refresh; read-mostly design; runs
  offline against the local DB.

### Phase 12 — Testing & Validation
- `tests/integration/`: an end-to-end simulated paper-trading day. `tests/stress/`: flash-crash
  simulation, feed outage, order storm verifying the 10/min cap. Mutation spot-checks on safety
  thresholds. Coverage ≥ 85% on `risk/` and `trading/`.

### Phase 13 — Optimization
- Profiling-driven vectorization of hot paths, indicator/feature caching, DB indices on hot
  queries, benchmark script with results recorded in docs.

---

## 6. FUNCTIONAL VERIFICATION — RUN IT, DON'T JUST READ IT

### 6.1 Config pins (assert each against `config.yaml` / `AppConfig`)
| Area | Expected values |
|---|---|
| Daily loss ladder | −0.01 / −0.015 / −0.02 / −0.03 → YELLOW / ORANGE / RED / EMERGENCY |
| Weekly loss | −0.03 / −0.05 / −0.07 |
| Monthly loss | −0.05 / −0.08 / −0.12 |
| Drawdown ladder | −0.05 / −0.08 / −0.12 / −0.15 |
| VIX ladder | 20 → −25% size, 25 → −50%, 30 → −75%, 40 → exit all; intraday spike 20% |
| Market crash | −2% YELLOW, −3% ORANGE, −5% RED; sector crash −5% → block sector 3 days |
| Flash crash | −1% within 5 min → pause 10 min; resume after 50% recovery |
| Technical | feed timeout 120s, emergency 300s; API retries 3; model min confidence 0.40; 10 orders/min; duplicate window 30s; max attempts 3 |
| Recovery | day 1–3: 25%, day 4–7: 50%, week 2: 75%, week 3+: 100%; `cooling_off_days: 5` |
| Signal weights | technical 0.30, ML 0.35, sentiment 0.20, patterns 0.10, regime 0.05 (sum = 1.00) |
| Watchlist | AAPL MSFT GOOGL TSLA NVDA AMZN META NFLX SPY QQQ |
| Modes | `trading.mode: paper`, `automation_mode: semi_automated`, `broker.name: paper_only` |

### 6.2 Database
Fresh tmp DB → migrations apply; run migration twice (idempotent); 16 tables present; breaker_state
round-trips across instances; audit tables accept inserts; `insert_news` dedup returns sane ids;
`close_paper_trade` realized P&L includes entry fees.

### 6.3 Data agent
Fake-provider sync of 3 symbols → bars + fundamentals + macro rows inserted; lookback caps enforced
(attempt 1m years=1 → clamped to 7d with warning); 4h resample anchored to session's first bar;
universe fetch works offline via static fallback.

### 6.4 Circuit breakers (safety-critical — demonstrate EACH live, with audit rows)
- Simulated −2.2% day → HALTED + lock + 2 audit rows; state restored by a NEW manager instance.
- −3.5% day → EMERGENCY; resume REJECTED without token; accepted with token.
- Kill switch → cancel-all + flatten-all actions emitted; human-gated resume.
- VIX ladder 20/25/30/40 → multipliers 0.75/0.50/0.25/0; VIX falling auto-clears (condition trigger).
- Flash crash −1% in 5 min → pause 10 min; auto-resume path after 50% recovery.
- Sticky vs condition: HALTED from VIX alone auto-recovers; HALTED from daily loss does NOT.
- Breakers cannot be disabled: no config/env path bypasses the manager or gateway — grep to prove it.

### 6.5 Later phases (only if implemented — otherwise list as ❌)
- Indicator vectors: RSI/EMA/MACD on a fixed series match hand-computed values.
- Backtest on a bundled sample series: assert every fill timestamp > signal timestamp (anti-look-ahead
  test exists and passes); purged CV has embargo > 0.
- Paper-trading dry run end-to-end: signals → gateway → paper fills → positions → P&L rows in DB.
- Dashboard boots headless for 30s without exceptions.

### 6.6 Look-ahead audit (phases ≥ 2/3/6)
`grep -RIn "\.shift(-" --include="*.py" .` → must be empty; scalers/imputers fit ONLY on training
fold; feature pipeline asserts index alignment; walk-forward folds never overlap test windows.

---

## 7. TEST-SUITE QUALITY AUDIT

- 100% pass, twice in a row, same counts; no live network (grep tests for `http`, `yf.download`,
  `requests.get` without mocks/fixtures); fixed seeds; wall-clock only via injected clocks.
- Anti-vacuity: read 20 sampled tests across modules — each must assert real behavior (values,
  state transitions, raised errors), not existence/`is not None`.
- Mutation spot-check: temporarily change the daily-loss RED threshold −0.02 → −0.025 in a COPY of
  the config → at least one test MUST fail; revert afterwards.
- Coverage report (`pytest --cov`): `risk/` and `trading/` ≥ 85%; note overall number in report.

---

## 8. SAFETY INVARIANTS — NON-NEGOTIABLE (verify, never weaken)

1. Default mode is paper trading; `broker.name: paper_only`.
2. Live trading requires ALL of: ≥90 days paper history, Sharpe ≥ 1.0, max drawdown ≤ 15%,
   win rate ≥ 50%, all breakers tested, and explicit human authorization — enforced in CODE,
   not docs.
3. Circuit breakers / kill switch cannot be disabled by config, env var, or code path.
4. Kill switch = cancel all pending + flatten all + human-gated (token-confirmed) resume.
5. Secrets only via environment; `.env` gitignored; `.env.example` documents every variable.

---

## 9. FIX POLICY

For EVERY ❌ or ⚠️: implement it properly in this session (full production code, docstrings, type
hints, logging, unit tests behaving per spec). Never "fix" by deleting the check, weakening a
threshold, or writing a vacuous test. Re-run §3 to 100% green after fixes.

---

## 10. DELIVERABLES

1. `docs/AUDIT_REPORT.md` committed to the repo, containing:
   - Per-phase table: `Item | Plan location | Expected | Found | Verdict ✅/⚠️/❌ | Evidence (cmd/test)`
   - Stub-sweep results (every hit + disposition)
   - Functional-run transcript summaries (§6), incl. breaker scenario outputs
   - Coverage numbers; determinism proof (two identical runs)
   - Deviations from spec + why (safer option chosen)
   - Re-answer of the 7-item self-check checklist (automated trading; paper trading; per-asset/
     per-strategy/portfolio exposure limits; speed breakers daily/weekly/monthly/per-strategy/
     per-asset/drawdown; kill switch manual+auto with cancel+flatten+human-gated resume; broker
     abstraction with pluggable adapters + retry/timeout + risk-gateway-before-transmission;
     human oversight & configurability) — each **Yes** with file:line references.
2. All fixes implemented and tested.
3. One pull request titled `Audit: verification report + fixes` (branch per session convention),
   body summarizing verdicts per phase and linking the report.

Do not ask for permission at any point — audit, fix, test, PR, then summarize in chat.
