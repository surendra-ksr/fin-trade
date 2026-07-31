# Phase 15 Evidence — Alpaca Paper Sandbox

**Date:** 2026-07-31  
**Branch:** `arena/019fb88c-fin-trade`  
**Implementation commit:** `c0e95d5412a0676307108637d7435d55db03cf74` (`Phase 15: alpaca paper sandbox`), pushed immediately to `origin/arena/019fb88c-fin-trade`.

## Reconciliation

```text
Baseline Phase 14 TOTAL = 548
Phase 15 additions     = 21 tests
CURRENT TOTAL          = 569

TOTAL = CORE_GREEN(556) + ML_ONLY(12) + OPT_ONLY(1) = 569
```

The 21 additions are 20 new Phase-15 tests plus one Phase-10 broker-gate regression added while updating the Alpaca paper/live construction contract.

## Implementation inventory

| File | Phase-15 purpose |
| --- | --- |
| `utils/config.py` / `config.yaml` | `broker.alpaca` nested config: `base_url`, `mode`, Alpaca retry/timeout fields; unknown mode rejected. |
| `.env.example` | Documents `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` as blank env-only paper credentials. |
| `trading/alpaca_adapter.py` | Explicit `base_url`; fail-closed URL/live-mode gate; nested retry policy; mock pagination/error payloads. |
| `automation/reconcile.py` | Optional `reconcile_alpaca_paper()` DB-vs-Alpaca-paper compare reusing the sticky `POSITION_MISMATCH` halt path. |
| `scripts/alpaca_sandbox_smoke.py` | Operator-run redacted paper sandbox smoke with `--dry-run` and clear exit codes. |
| `tests/unit/test_phase15_alpaca_sandbox.py` | 20 mocked zero-network tests for config, URL gate, mock semantics, reconciliation, smoke redaction/exit codes. |
| `tests/unit/test_phase10_broker.py` | Updated/expanded build-broker paper-vs-live URL gate tests. |
| `docs/BUILD_PLAN.md`, `docs/ARCHITECTURE.md`, `docs/AUDIT_REPORT.md` | Phase-15 docs cycle. |

## Committed-state proof

Raw command transcript is committed in `docs/PHASE15_CORE_EVIDENCE_RAW.txt`.

```console
$ git status --short

$ git log --oneline -3
c0e95d5 Phase 15: alpaca paper sandbox
bb4b39b Phase 14: CI & ops hardening

$ git rev-parse HEAD
c0e95d5412a0676307108637d7435d55db03cf74

$ wc -l scripts/alpaca_sandbox_smoke.py tests/unit/test_phase15_alpaca_sandbox.py
  292 scripts/alpaca_sandbox_smoke.py
  314 tests/unit/test_phase15_alpaca_sandbox.py
  606 total
```

## Verbatim demanded bodies

### Fail-closed Alpaca URL gate

```python
    def __init__(
        self,
        *,
        config: Optional[AppConfig] = None,
        client: Any = None,
        gateway: Optional[RiskGateway] = None,
        base_url: Optional[str] = None,
        live_gate_evidence: Optional[LiveGateEvidence] = None,
    ) -> None:
        self.config = config or load_config()
        self.gateway = gateway or RiskGateway(config=self.config)
        alp = self.config.broker.alpaca
        self.base_url = str(base_url or alp.base_url or self.config.broker.alpaca_base_url)
        self.alpaca_mode = str(alp.mode or "paper").lower()
        if self._requires_live_gate():
            gate = evaluate_live_gate(
                self.config,
                live_gate_evidence or LiveGateEvidence(),
                broker_name="alpaca",
            )
            gate.raise_if_denied()
        self._client = client if client is not None else self._build_live_client()

    def _requires_live_gate(self) -> bool:
        """Fail-closed URL gate: live mode or non-paper URL requires all-pass live evidence."""
        return self.alpaca_mode == "live" or not is_paper_base_url(self.base_url)
```

### Alpaca-paper reconciliation compare

```python
def reconcile_alpaca_paper(
    db: DatabaseManager,
    *,
    adapter: Any = None,
    config: Optional[AppConfig] = None,
    breaker: Any = None,
    portfolio_id: str = "default",
    tolerance: float = DEFAULT_TOLERANCE,
    now_fn: Optional[Callable[[], datetime]] = None,
    adapter_enabled: Optional[bool] = None,
) -> ReconcileResult:
    """Optionally compare DB positions against Alpaca paper adapter positions.

    The comparison is enabled when ``adapter_enabled`` is true, or by config
    when ``broker.name == 'alpaca'`` and ``broker.alpaca.mode == 'paper'``.
    When disabled, the broker is not touched and a skipped row is written to
    ``automation_log``.  When enabled, this delegates to
    :func:`reconcile_positions`, so mismatches use the existing
    ``POSITION_MISMATCH`` sticky-halt path and the same detailed
    ``automation_log`` payload as startup reconciliation.
    """
    cfg = config or load_config()
    now = (now_fn or utc_now)()
    enabled = bool(adapter_enabled) if adapter_enabled is not None else (
        str(cfg.broker.name).lower() == "alpaca"
        and str(cfg.broker.alpaca.mode).lower() == "paper"
    )
    if not enabled or adapter is None:
        result = ReconcileResult(halted=False, summary="alpaca_paper_compare=skipped")
        try:
            db.log_automation(
                "reconcile",
                "alpaca_paper_compare",
                "skipped",
                details={
                    "enabled": enabled,
                    "adapter_present": adapter is not None,
                    "at": to_iso_z(now),
                },
                timestamp=now,
            )
        except Exception as exc:
            _log.warning("could not persist skipped Alpaca reconciliation: {}", exc)
        return result

    try:
        broker_positions = adapter.positions()
    except Exception as exc:
        try:
            db.log_automation(
                "reconcile",
                "alpaca_paper_compare",
                "error",
                details={"error": str(exc), "at": to_iso_z(now)},
                timestamp=now,
            )
        except Exception:
            pass
        raise

    return reconcile_positions(
        db,
        broker_positions,
        config=cfg,
        breaker=breaker,
        portfolio_id=portfolio_id,
        tolerance=tolerance,
        now_fn=lambda: now,
        routine="reconcile",
        action="alpaca_paper_compare",
    )
```

## Security proof: zero key material

Security rules followed:

- No real Alpaca/API keys requested, accepted, stored, printed, or committed.
- The smoke script accepts no key CLI arguments.
- The real smoke reads credentials only from `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY` in env / `.env`.
- `.env.example` contains blank placeholders only.
- Exact grep proof was run after removing the in-repo `.venv` so dependency strings do not create false positives. The matches below are variable names/placeholders and documentation labels only; there are no `PK...` or `SK...` credential values.

```console
$ grep -RInE '(APCA|ALPACA|PK[A-Z0-9]{18}|SK[A-Z0-9]{18})' .
./.env.example:67:APCA_API_KEY_ID=""
./.env.example:68:APCA_API_SECRET_KEY=""
./.env.example:72:ALPACA_BASE_URL=""
./config.yaml:441:  alpaca_api_key: "${APCA_API_KEY_ID}"
./config.yaml:442:  alpaca_secret_key: "${APCA_API_SECRET_KEY}"
./docs/ARCHITECTURE.md:516:credentials only from `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY` loaded from the
./docs/AUDIT_REPORT.md:450:  `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY` in env/`.env`, never from chat/CLI,
./docs/BUILD_PLAN.md:264:- [x] `scripts/alpaca_sandbox_smoke.py` implements the ordered paper-sandbox smoke with `--dry-run`, explicit exit codes, account/positions, tiny market buy, fill wait, limit place/cancel, adapter kill switch, token-confirmed resume, and redacted transcript output. The real paper run reads credentials only from `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY` in the environment or local `.env`; no keys are accepted on CLI or printed.
./scripts/alpaca_sandbox_smoke.py:6:``APCA_API_KEY_ID`` / ``APCA_API_SECRET_KEY`` after loading a local ``.env``
./scripts/alpaca_sandbox_smoke.py:55:        return "REDACTED ALPACA PAPER SANDBOX TRANSCRIPT\n" + body
./scripts/alpaca_sandbox_smoke.py:76:    """True only when both APCA paper-sandbox credential env vars are set."""
./scripts/alpaca_sandbox_smoke.py:77:    return bool(os.environ.get("APCA_API_KEY_ID") and os.environ.get("APCA_API_SECRET_KEY"))
./scripts/alpaca_sandbox_smoke.py:144:        out.add("ABORT missing APCA_API_KEY_ID/APCA_API_SECRET_KEY in environment or .env")
./tests/unit/test_phase15_alpaca_sandbox.py:287:    assert "REDACTED ALPACA PAPER SANDBOX TRANSCRIPT" in captured
./tests/unit/test_phase15_alpaca_sandbox.py:296:    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
./tests/unit/test_phase15_alpaca_sandbox.py:297:    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
./tests/unit/test_phase15_alpaca_sandbox.py:301:    assert "missing APCA_API_KEY_ID/APCA_API_SECRET_KEY" in captured
./trading/alpaca_adapter.py:47:PAPER_ALPACA_HOST = "paper-api.alpaca.markets"
./trading/alpaca_adapter.py:54:    return host == PAPER_ALPACA_HOST
./trading/alpaca_adapter.py:211:        key = os.environ.get("APCA_API_KEY_ID") or self.config.api_keys.alpaca_api_key
./trading/alpaca_adapter.py:212:        secret = os.environ.get("APCA_API_SECRET_KEY") or self.config.api_keys.alpaca_secret_key
./trading/alpaca_adapter.py:215:                "alpaca credentials missing (APCA_API_KEY_ID / APCA_API_SECRET_KEY)"
```

## Collect-only proof

Complete raw outputs are committed as:

- `docs/PHASE15_CORE_EVIDENCE_RAW.txt` — core collect-only plus two core-green runs.
- `docs/PHASE15_ML_COLLECT_RAW.txt` — ML environment collect-only.
- `docs/PHASE15_OPT_COLLECT_RAW.txt` — optional environment collect-only.

Summaries:

```console
$ /tmp/ft-core/bin/python -m pytest --collect-only -q
========================= 569 tests collected in 0.23s =========================

$ /tmp/ft-ml/bin/python -m pytest --collect-only -q
========================= 569 tests collected in 0.28s =========================

$ /tmp/ft-opt/bin/python -m pytest --collect-only -q
========================= 569 tests collected in 0.23s =========================
```

## Green runs

Core-green command deselected only the known 12 ML-only bodies plus the 1 Streamlit boot body:

```console
$ /tmp/ft-core/bin/python -m pytest tests -q [13 env-gated deselects] (run 1)
===================== 556 passed, 13 deselected in 28.31s ======================

$ /tmp/ft-core/bin/python -m pytest tests -q [13 env-gated deselects] (run 2)
===================== 556 passed, 13 deselected in 29.06s ======================
```

Full environment (core + ML + optional dependencies) also ran the entire committed test set twice with distinct durations:

```console
$ /tmp/ft-ml/bin/python -m pytest tests -q
============================= 569 passed in 31.48s =============================

$ /tmp/ft-ml/bin/python -m pytest tests -q
============================= 569 passed in 30.19s =============================
```

Full run raw output is committed in `docs/PHASE15_FULL_GREEN_RAW.txt`.

## Pip freeze

Core freeze is in `docs/PHASE15_CORE_EVIDENCE_RAW.txt`. Key tier packages from the full environment:

```text
PyYAML==6.0.3
alpaca-py==0.43.5
numpy==2.2.6
optuna==4.5.0
pandas==2.2.3
plotly==6.0.1
pytest==8.3.5
scikit-learn==1.7.2
streamlit==1.42.0
torch==2.6.0
transformers==4.48.3
```

## Sandbox transcript section

### Dry-run transcript (zero network)

```console
$ /tmp/ft-core/bin/python scripts/alpaca_sandbox_smoke.py --dry-run --fill-timeout-seconds 1 --poll-seconds 0.01
REDACTED ALPACA PAPER SANDBOX TRANSCRIPT
mode=DRY-RUN mock base_url=https://paper-api.alpaca.markets
1 account id=PAPE***0000 status=ACTIVE equity=100000.00 cash=100000.00
2 positions count=0 symbols=[]
3 market_buy submitted order=sand***5a61 status=filled
4 market_buy filled order=sand***5a61 qty=1
5 limit_order submitted order=sand***ae91 status=submitted
6 limit_order cancel order=sand***ae91 status=cancelled
7 kill_switch cancelled=0 flattened=1
8 resume token_confirmed=True breaker_state=RESTRICTED
```

### Live Alpaca paper transcript

`PENDING-USER-RUN` — no paper credentials are available in this session and no transcript is fabricated.

Exact operator command (with credentials only in local `.env` / environment, never in the command):

```bash
python scripts/alpaca_sandbox_smoke.py --config config.yaml --symbol AAPL --quantity 1 --reference-price 150 --limit-price 1
```

## Gateway/no-network grep proofs

```console
$ grep -RInE 'requests\.|urlopen|httpx\.|urllib.request|socket\.' trading/alpaca_adapter.py scripts/alpaca_sandbox_smoke.py automation/reconcile.py tests/unit/test_phase15_alpaca_sandbox.py

$ grep -RIn 'broker.submit' risk trading scripts tests/unit/test_phase15_alpaca_sandbox.py
risk/position_limits.py:153:        return broker.submit(order)
```

The smoke script source is also asserted by test to use `adapter.place_order` and not low-level `.submit(`.

## Docs cycle proof

```console
$ git log --oneline -- docs/BUILD_PLAN.md docs/ARCHITECTURE.md docs/AUDIT_REPORT.md | head
c0e95d5 Phase 15: alpaca paper sandbox
bb4b39b Phase 14: CI & ops hardening
```

## Verdict

Phase 15 gate satisfied for implementation and mocked validation. The real Alpaca paper sandbox transcript remains `PENDING-USER-RUN` until an operator runs the exact command above with their local paper credentials and pastes a redacted transcript.
