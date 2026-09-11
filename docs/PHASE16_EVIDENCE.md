# Phase 16 Evidence — Environment & Clock Hardening

## Scope

- Test startup removes every `FIN_TRADE_*`, `APCA_*`, and `ALPACA_*` variable **before**
  application/test-module imports, and the autouse fixture repeats that isolation for
  each test.
- `FIN_TRADE_TEST=1` is installed at startup; `load_config()` does not read dotenv
  files while that sentinel is present.
- Empty `FIN_TRADE_CONFIG` is missing, not a path: both `load_config()` and
  `get_config()` use `os.getenv("FIN_TRADE_CONFIG") or DEFAULT_CONFIG_PATH`.
- The expiration test patches monotonic time before token creation, then advances the
  same mutable clock by `_OVERRIDE_TOKEN_TTL_SECONDS + 1`.
- CI now has a dedicated `dotenv-isolation` job which writes a sample `.env` with an
  empty config value and broker-shaped credentials before running the core suite.

## Clock reproduction and repair

Command run before the fixed focused test:

```console
$ python3 -c "import time; print(time.monotonic())"
83.97215782
$ pytest tests/unit/test_circuit_breakers.py::TestManualControls::test_override_token_expires -q
============================== 1 passed in 0.16s ===============================
```

The evidence runner had only 83.97 seconds of uptime, so it could not itself satisfy
an `>10880`-second reproduction precondition. The former test compared a real
`created_at` against a patched absolute `11000.0`; therefore it fails whenever the
host's real monotonic uptime is greater than `11000 - 120 = 10880` seconds. The fixed
test has no host-uptime dependency and passed in every full-suite run below.

Repository-wide audit command and result after the repair:

```console
$ grep -RInE 'setattr\([^\n]*(monotonic|time|datetime)[^\n]*' . --exclude-dir=.git --exclude-dir=.venv
./tests/unit/test_circuit_breakers.py:578:        monkeypatch.setattr(cb_mod.time, "monotonic", lambda: fake_now["t"])
```

That remaining fake is relative: token creation and expiration both read the same
mutable `fake_now["t"]`; the test advances it only by the TTL.

## Sample dotenv regression run

The sample dotenv used in the matrix's dotenv columns was:

```dotenv
FIN_TRADE_CONFIG=
APCA_API_KEY_ID=dotenv-must-not-leak
ALPACA_SECRET_KEY=dotenv-must-not-leak
```

The full 569-test run with that file present passed in both time zones (shown below),
which includes the staged environment-isolation regression coverage.

## Four-quadrant matrix

Commands were run from the repository root after installing the pinned base, ML, and
optional requirements:

| TZ | `.env` state | Command | Result |
| --- | --- | --- | --- |
| `UTC` | absent | `TZ=UTC pytest tests -q` | `569 passed in 31.30s` |
| `UTC` | sample present | `TZ=UTC pytest tests -q` | `569 passed in 30.79s` |
| `Asia/Kolkata` | sample present | `TZ=Asia/Kolkata pytest tests -q` | `569 passed in 31.69s` |
| `Asia/Kolkata` | absent | `TZ=Asia/Kolkata pytest tests -q` | `569 passed in 30.42s` |

Each command collected 569 tests and passed all 569.

## Exact reconciliation

```text
TOTAL = CORE_GREEN(556) + ML_ONLY(12) + OPT_ONLY(1) = 569
UTC / no dotenv:             569 collected = 569 passed + 0 failed
UTC / sample dotenv:         569 collected = 569 passed + 0 failed
Asia/Kolkata / sample dotenv:569 collected = 569 passed + 0 failed
Asia/Kolkata / no dotenv:    569 collected = 569 passed + 0 failed
```

The tier reconciliation matches the established Phase 15 inventory. The matrix runs
used the fully provisioned environment, so ML-only and optional tests ran rather than
being deselected.
