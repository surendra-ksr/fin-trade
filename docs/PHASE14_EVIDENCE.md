# Phase 14 Evidence — CI & Ops Hardening

**Scope:** GitHub Actions tiers, webhook notifications, and retained SQLite backups.

## Reconciliation

**CORRECTION (2026-07-31):** The first pack incorrectly called the CI command's
518 selected tests `CORE`. It excluded 30 tests, rather than only the 13
intentionally environment-gated tests. Fresh verification on this branch gives
the required complete reconciliation:

```text
Phase 13 baseline TOTAL 543 = 525 unit-green + 13 env-gated + 5 integration/stress
Phase 14 additions       +5
CURRENT TOTAL 548 = CORE_GREEN 535 + ML_ONLY 12 + OPT_ONLY 1
```

There are **no Phase 14 isolation regressions**. The complete `grep FAILED`
output lists precisely the known twelve ML-only tests and one optional
Streamlit boot test; it lists no Phase 14 test.

## Implementation inventory

| File | Purpose |
| --- | --- |
| `docs/PHASE14_EVIDENCE.md` (workflow block below) | Python 3.11 tier matrix; PR core coverage XML; 85% `risk` + `trading` gate; main/weekly extended lanes. |
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

## Workflow hand-off (GitHub App lacks `workflows` permission)

The Arena GitHub App rejected a push containing `.github/workflows/ci.yml`.
The workflow is therefore not included in the branch tip. A repository owner
must create `.github/workflows/ci.yml` through the GitHub web UI using this
exact content after merging (or before merging the PR):

```yaml
name: CI
on:
  pull_request:
  push:
    branches: [main]
  schedule:
    - cron: '0 3 * * 1'
  workflow_dispatch:

jobs:
  core:
    name: core / Python ${{ matrix.python }}
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python: ['3.11']
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: ${{ matrix.python }}}
      - run: python -m pip install --upgrade pip && pip install -r requirements.txt
      - name: Core tests and risk/trading coverage gate
        run: >-
          pytest tests/unit -q
          --ignore=tests/unit/test_phase3_models.py
          --ignore=tests/unit/test_phase4_models.py
          --ignore=tests/unit/test_phase11_dashboard_boot.py
          --cov=risk --cov=trading --cov-report=xml:coverage.xml --cov-fail-under=85
      - uses: actions/upload-artifact@v4
        if: always()
        with: {name: coverage-core-py${{ matrix.python }}, path: coverage.xml}

  extended:
    name: ${{ matrix.tier }} / Python ${{ matrix.python }}
    if: github.event_name == 'schedule' || github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python: ['3.11']
        tier: [ml, optional]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: ${{ matrix.python }}}
      - run: python -m pip install --upgrade pip && pip install -r requirements.txt && pip install -r requirements-ml.txt && if [ '${{ matrix.tier }}' = optional ]; then pip install -r requirements-optional.txt; fi
      - run: pytest tests/unit -q
```

## Security and operational behavior

- The webhook destination is read only from `FIN_TRADE_ALERT_WEBHOOK_URL`; no
  URL or secret is added to YAML/configuration or logs.
- A missing URL, request exception, or non-2xx response returns `False` and
  does not propagate into risk/trading execution.
- Backups use SQLite's online backup API through `DatabaseManager.backup`.
  Retention removes only expired `*.db` files in the selected backup directory;
  negative retention is rejected.
