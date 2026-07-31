# PHASE 3 SESSION OPENER — `fin-trade`

> PASTE THIS ENTIRE BLOCK as the first message of your new coding session for repo
> `surendra-ksr/fin-trade`.

## CONTEXT (repo state)

`main` contains Phase 1 (PR #1) and Phase 2 (PR #2, merged): foundation + `risk/circuit_breakers.py`,
`data/database.py`, `agents/data_agent.py` with provider extensions, `features/indicators.py`,
`features/feature_engineer.py`, tiered requirements (`requirements.txt` core / `-ml` / `-optional`),
and governance docs. READ FIRST, in order: `docs/BUILD_PLAN.md` (plan of record),
`docs/EVIDENCE_PROTOCOL.md` (your mandatory reporting rules), `docs/AUDIT_REPORT.md` (verdicts).
Last known state: 294 tests passing; HEAD had `docs/EVIDENCE_PROTOCOL.md` on top.

## RULES (absolute)

Priorities: Correctness > Safety > Performance. Full production code — zero stubs — with
docstrings, type hints, logging, and behavioral tests for every module; 100% suite green TWICE
in the clean pinned env before any PR leaves draft; **every commit pushed in the same step**;
atomic evidence pack per `docs/EVIDENCE_PROTOCOL.md`, with `date -u +%T` executed and pasted
BEFORE each command group — recycled or byte-identical output blocks (e.g., identical run
durations across reports) AUTO-FAIL the phase; zero narrative claims without pasted fresh
command output; no rewriting pushed history; no relays until a phase gate is fully satisfied;
never ask for permission between phases; never weaken safety thresholds or bypass the breaker
manager / future risk gateway.

## TASK 0 — CLOSE THE PHASE-2 EVIDENCE DEBT (blocks all Phase-3 work)

Run FRESH, date-stamped, unedited:

1. `sed -n` the FULL bodies of `adx_dmi`, `parabolic_sar`, `ichimoku`, and every volume
   indicator (`obv`/vwap/`chaikin_ad`/`cmf`/`volume_zscore`) from `features/indicators.py`.
   ADX must show the full Wilder construction (+DM/−DM suppression, TR, three smoothed series,
   DX → ADX); PSAR must accelerate AF and CLAMP at `maximum`; Ichimoku spans must displace +26;
   each volume function must be a real standalone implementation. **Anything compressed,
   trivial, or wrong: fix it properly, add numeric vector tests, commit + push.**
2. FULL `pytest --collect-only -q` — the entire list, no pipes, no truncation. Duplicate or
   vacuous test names → fix.
3. Resolve the 282-collection anomaly: why collection showed 282 tests while
   `tests/unit/test_phase2_providers_quality.py` existed untracked, same count as without it —
   prove with `git log --follow` for that file + current `git status`.
4. `git log -p --follow -- features/feature_engineer.py | head -100` explaining the 79 → 59
   line shrink; if real logic was lost, restore it and add the tests that should have caught it.
5. `pip freeze` of the pinned core env + the full runtime demo WITH the heredoc script text
   visible (RSI vector must equal 86.20689655172414) — script and stdout both pasted.
6. Update `docs/AUDIT_REPORT.md` Phase-2 verdicts citing this fresh evidence; commit + push.

## TASK 1 — PHASE 3 COMPLETION (Core models) per `docs/BUILD_PLAN.md`

- `models/base.py` finalized: ABC (`fit`/`predict`/`save`/`load`) + versioned registry persisted
  in the DB with a round-trip test.
- LSTM + GRU (torch from `requirements-ml.txt`): configurable layers/dropout; tests for output
  shapes, seed determinism, and single-batch overfit smoke.
- GBM baseline: fit/predict/save/load test.
- Trainer: walk-forward split generator + purged, embargoed K-fold CV (embargo > 0); tests PROVE
  zero train/test index overlap in every fold and the embargo gap; windows advance correctly.
- Sequence builder: features at time t use ONLY data ≤ t — dedicated anti-leak test with a
  planted future spike that must not change any earlier feature row.
- Metrics registry: rmse/mae/mape/directional-accuracy validated on known arrays.
- GATE (atomic pack, fresh, date-stamped): per-module wc -l + docstring greps; FULL collect
  list; 2× suite green — state exactly which env (core vs ml) ran which test files + both
  `pip freeze`s; docs updated (`BUILD_PLAN.md` checkboxes, `ARCHITECTURE.md` roadmap,
  `AUDIT_REPORT.md` Phase-3 entry); then PR **"Phase 3: core models"**.

## TASK 2+ — PHASES 4 → 13

Continue in BUILD_PLAN order (advanced models → sentiment/patterns → backtesting → risk limits &
gateway → orders + paper trading → automation → broker integration → dashboard → testing &
validation → optimization), one PR per phase, same gate, no mid-phase relays, no permission-
asking. If the session closes on a PR merge, the next session resumes from these docs.
