"""Tests for utils/config.py — loading, substitution, validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from utils.config import (
    AppConfig,
    ConfigError,
    ConfigValidationError,
    _ENV_PATTERN,
    _substitute_env,
    get_config,
    load_config,
)

ROOT = Path(__file__).resolve().parents[2]


class TestLoadMasterConfig:
    def test_repo_config_loads_and_validates(self) -> None:
        cfg = load_config(ROOT / "config.yaml")
        assert cfg.trading.mode == "paper"
        assert cfg.trading.automation_mode == "semi_automated"
        assert cfg.circuit_breakers.enabled is True

    def test_master_config_values(self) -> None:
        cfg = load_config(ROOT / "config.yaml")
        assert cfg.risk.max_position_size_pct == pytest.approx(0.10)
        assert cfg.risk.daily_loss_limit_pct == pytest.approx(0.02)
        assert cfg.circuit_breakers.daily_loss.level4_pct == pytest.approx(-0.03)
        assert cfg.circuit_breakers.vix.exit_all == pytest.approx(40.0)
        assert cfg.order_limits.per_day.max_trades_per_day == 20
        assert cfg.order_limits.per_stock.min_price == pytest.approx(5.0)
        assert cfg.signal_weights.ml_models == pytest.approx(0.35)
        assert sum((cfg.signal_weights.technical, cfg.signal_weights.ml_models,
                    cfg.signal_weights.sentiment, cfg.signal_weights.fundamental,
                    cfg.signal_weights.macro)) == pytest.approx(1.0, abs=1e-9)
        assert cfg.paper_trading.paper_mode.value == "full_auto"
        assert cfg.recovery.day1_3_size_pct == pytest.approx(0.25)

    def test_breaker_ladders_descend(self) -> None:
        cfg = load_config(ROOT / "config.yaml")
        dl = cfg.circuit_breakers.daily_loss
        assert dl.level1_pct > dl.level2_pct > dl.level3_pct > dl.level4_pct
        dd = cfg.circuit_breakers.drawdown
        assert dd.level1_pct > dd.level2_pct > dd.level3_pct > dd.level4_pct

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError):
            load_config(tmp_path / "nope.yaml")

    def test_non_mapping_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("- just\n- a\n- list\n")
        with pytest.raises(ConfigError):
            load_config(bad)


class TestEnvSubstitution:
    def test_pattern_expands_present_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FT_TEST_KEY", "abc123")
        warnings: list[str] = []
        assert _substitute_env("${FT_TEST_KEY}", warnings) == "abc123"
        assert warnings == []

    def test_missing_var_becomes_empty_with_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FT_MISSING", raising=False)
        warnings: list[str] = []
        assert _substitute_env("${FT_MISSING}", warnings) == ""
        assert any("FT_MISSING" in w for w in warnings)

    def test_default_syntax(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FT_MISSING2", raising=False)
        warnings: list[str] = []
        assert _substitute_env("${FT_MISSING2:-hello}", warnings) == "hello"
        assert warnings == []

    def test_nested_structures(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FT_NESTED", "X")
        data = {"a": ["${FT_NESTED}", {"b": "${FT_NESTED}suffix"}], "c": 5}
        out = _substitute_env(data, [])
        assert out == {"a": ["X", {"b": "Xsuffix"}], "c": 5}

    def test_regex_no_partial_match(self) -> None:
        assert _ENV_PATTERN.sub(lambda m: "Y", "$NOTAVAR and ${PROPER_1}") == \
            "$NOTAVAR and Y"


class TestValidation:
    def _write(self, tmp_path: Path, mutate) -> Path:
        data = yaml.safe_load((ROOT / "config.yaml").read_text())
        mutate(data)
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump(data))
        return path

    def test_weight_sum_enforced(self, tmp_path: Path) -> None:
        def m(d):
            d["signal_weights"]["technical"] = 0.9
        with pytest.raises(ConfigValidationError) as err:
            load_config(self._write(tmp_path, m))
        assert any("sum" in p for p in err.value.problems)

    def test_breaker_ladder_order_enforced(self, tmp_path: Path) -> None:
        def m(d):
            d["circuit_breakers"]["daily_loss"]["level2_pct"] = -0.005
        with pytest.raises(ConfigValidationError) as err:
            load_config(self._write(tmp_path, m))
        assert any("daily_loss" in p for p in err.value.problems)

    def test_invalid_mode_rejected(self, tmp_path: Path) -> None:
        def m(d):
            d["trading"]["mode"] = "yolo"
        with pytest.raises(ConfigValidationError):
            load_config(self._write(tmp_path, m))

    def test_empty_watchlist_rejected(self, tmp_path: Path) -> None:
        def m(d):
            d["watchlist"] = {"use_sp500": False, "use_nasdaq100": False,
                              "use_russell2000": False, "custom_stocks": []}
        with pytest.raises(ConfigValidationError) as err:
            load_config(self._write(tmp_path, m))
        assert any("watchlist" in p for p in err.value.problems)

    def test_live_requires_broker_and_authorization(self, tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FIN_TRADE_LIVE_AUTHORIZATION", raising=False)

        def m(d):
            d["trading"]["mode"] = "live"
        with pytest.raises(ConfigValidationError) as err:
            load_config(self._write(tmp_path, m))
        joined = " ".join(err.value.problems)
        assert "broker" in joined and "FIN_TRADE_LIVE_AUTHORIZATION" in joined

    def test_automation_schedule_order_enforced(self, tmp_path: Path) -> None:
        def m(d):
            d["automation"]["stop_new_entries"] = "09:00"  # before market_open
        with pytest.raises(ConfigValidationError) as err:
            load_config(self._write(tmp_path, m))
        assert any("automation schedule" in p for p in err.value.problems)

    def test_unknown_keys_warn_not_fail(self, tmp_path: Path) -> None:
        def m(d):
            d["trading"]["nonsense_flag"] = True
        cfg = load_config(self._write(tmp_path, m))
        assert any("nonsense_flag" in w for w in cfg.warnings)


class TestConfigAccessors:
    def test_singleton_caches(self) -> None:
        a = get_config(ROOT / "config.yaml", reload=True)
        b = get_config(ROOT / "config.yaml")
        assert a is b

    def test_resolve_path(self) -> None:
        cfg = load_config(ROOT / "config.yaml")
        resolved = cfg.resolve_path("data/trading.db")
        assert str(resolved).endswith("data/trading.db")
        assert resolved.is_absolute()

    def test_redaction_masks_secrets(self) -> None:
        cfg = AppConfig()
        cfg.api_keys.alpaca_api_key = "PKSUPERSECRET"
        cfg.notifications.smtp_password = "hunter2"
        redacted = cfg.redacted()
        assert "SUPERSE" not in str(redacted["api_keys"])
        assert redacted["notifications"]["smtp_password"] == "***"
        # raw dict retains actual values (redaction is a view)
        assert cfg.to_dict()["api_keys"]["alpaca_api_key"] == "PKSUPERSECRET"

    def test_validate_passes_on_defaults(self) -> None:
        cfg = AppConfig()
        assert cfg.validate() == []

    def test_timeframes_all(self) -> None:
        cfg = AppConfig()
        assert cfg.timeframes.all_timeframes == ["1d", "1h", "4h", "1w"]
