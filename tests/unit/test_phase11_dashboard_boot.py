"""Phase 11 dashboard — headless boot smoke (OPTIONAL tier).

This module declares the new **OPT_ONLY** category: it requires Streamlit
(``requirements-optional.txt``), which is NOT installed in the clean CORE
environment. To mirror the ML_ONLY pattern, the Streamlit import happens
inside the test body, so:

* CORE env  -> the module COLLECTS (counts toward TOTAL) but the body fails
  with ``ModuleNotFoundError: streamlit`` -> counted as OPT_ONLY.
* optional env (streamlit installed) -> the body runs and proves the
  dashboard boots headless within the configured deadline.

Reconciliation after Phase 11:
    TOTAL = CORE_GREEN + ML_ONLY(12) + OPT_ONLY
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: Marks every test in this module as OPT_ONLY (Streamlit-required).
pytestmark = pytest.mark.optional

_BOOT_MARKERS = ("You can now view", "Local URL:", "Network URL:")
_BOOT_DEADLINE_S = 30


@pytest.mark.optional
def test_dashboard_headless_boot_smoke(tmp_path):
    """Headless 30s boot smoke: launch the Streamlit entry and confirm boot.

    Launches ``streamlit run dashboard/app.py`` fully headless (port 0, no
    browser, no file watcher, no usage stats) pointed at a seeded tmp DB via
    ``FIN_TRADE_DASHBOARD_DB``. Fails if the boot banner does not appear
    within the deadline; the captured output is surfaced verbatim.
    """
    import streamlit  # noqa: F401  -- OPT_ONLY gate: fails in CORE env
    from data.database import DatabaseManager  # seed the tmp DB so boot has data

    db_path = tmp_path / "boot_smoke.db"
    db = DatabaseManager(str(db_path))
    db.upsert_performance_metric("2026-07-30", 105_000.0, cash=40_000.0,
                                 daily_return=0.005)
    db.insert_paper_trade("AAPL", "BUY", 10, "2026-07-30", 190.0, strategy="smoke")
    db.close()

    env = dict(os.environ)
    env.update({
        "FIN_TRADE_DASHBOARD_DB": str(db_path),
        "FIN_TRADE_CONFIG": str(ROOT / "config.yaml"),
        "PYTHONPATH": str(ROOT),
        "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
    })
    cmd = [
        sys.executable, "-m", "streamlit", "run", str(ROOT / "dashboard" / "app.py"),
        "--server.headless=true",
        "--server.port=0",
        "--server.runOnSave=false",
        "--server.fileWatcherType=none",
        "--browser.gatherUsageStats=false",
    ]
    proc = subprocess.Popen(
        cmd, cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    captured: list[str] = []
    booted = False
    deadline = time.monotonic() + _BOOT_DEADLINE_S
    try:
        assert proc.stdout is not None
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
                continue
            captured.append(line.rstrip("\n"))
            if any(marker in line for marker in _BOOT_MARKERS):
                booted = True
                break
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    output = "\n".join(captured)
    assert booted, (
        f"dashboard did not boot within {_BOOT_DEADLINE_S}s.\n--- captured output ---\n"
        f"{output}\n--- end ---")
    # Smoke proof: the boot banner + the configured DB path appear in output.
    assert any("fin-trade" in line.lower() or "streamlit" in line.lower()
               for line in captured)
