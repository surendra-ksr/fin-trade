# Phase 14 Evidence — CI & Ops Hardening

**Scope:** GitHub Actions tiers, webhook notifications, and retained SQLite backups.

## Reconciliation

The Phase 13 reported baseline is **TOTAL 543** tests. Phase 14 adds five
fully mocked/local operational tests, so the current committed-state target is:

```text
CORRECTION: baseline TOTAL 543 -> current TOTAL 548
TOTAL 548 = CORE 518 + ML_ONLY/OPTIONAL 30
```

`CORE` is the dependency-minimal PR command in `.github/workflows/ci.yml`.
It deliberately excludes the ML-only Phase 3/4 modules and the Streamlit-only
boot smoke test. The extended CI job installs ML dependencies for both
extended lanes and additionally installs optional dependencies in the optional
lane; it runs on `main` pushes and the weekly schedule.

## Implementation inventory

| File | Purpose |
| --- | --- |
| `.github/workflows/ci.yml` | Python 3.11 tier matrix; PR core coverage XML; 85% `risk` + `trading` gate; main/weekly extended lanes. |
| `automation/notify.py` | `FIN_TRADE_ALERT_WEBHOOK_URL`-configured, fail-safe webhook breaker and digest alerts. |
| `scripts/backup_db.py` | SQLite online backup with timestamped names and bounded `.db` retention. |
| `tests/unit/test_phase14_ops.py` | Five local tests; HTTP is injected and mocked, and SQLite uses `tmp_path`. |

## Verification commands and unedited results

```console
$ .venv/bin/python -m pytest tests/unit/test_phase14_ops.py -q
collected 5 items

tests/unit/test_phase14_ops.py .....

============================== 5 passed in 0.10s ===============================
```

```console
$ .venv/bin/python -m pytest tests/unit -q --ignore=tests/unit/test_phase3_models.py --ignore=tests/unit/test_phase4_models.py --ignore=tests/unit/test_phase11_dashboard_boot.py --cov=risk --cov=trading --cov-report=xml:/tmp/coverage.xml --cov-fail-under=85
collected 518 items

============================= 518 passed in 46.84s =============================
Required test coverage of 85% reached. Total coverage: 90.61%
Coverage XML written to file /tmp/coverage.xml
```

```console
$ .venv/bin/python -m pytest --collect-only -q
========================= 548 tests collected in 0.27s =========================
```

The core command is intentionally exercised in the same Python 3.11 pinned
environment used by CI. The local core `requirements.txt` environment cannot
run the 30 extended tests because those require Torch, scikit-learn, Optuna,
and Streamlit; this is precisely why the CI tiers install those packages only
outside the PR core gate.

## Security and operational behavior

- The webhook destination is read only from `FIN_TRADE_ALERT_WEBHOOK_URL`; no
  URL or secret is added to YAML/configuration or logs.
- A missing URL, request exception, or non-2xx response returns `False` and
  does not propagate into risk/trading execution.
- Backups use SQLite's online backup API through `DatabaseManager.backup`.
  Retention removes only expired `*.db` files in the selected backup directory;
  negative retention is rejected.
