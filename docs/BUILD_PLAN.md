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
