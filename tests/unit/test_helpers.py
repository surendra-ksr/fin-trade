"""Tests for utils/helpers.py — calendar, math, OHLCV, containers."""

from __future__ import annotations

from datetime import date, datetime, time as dt_time, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from utils.helpers import (
    RateLimiter,
    add_trading_days,
    calmar_ratio,
    chunked,
    clamp,
    current_drawdown,
    deep_merge,
    dedupe_preserve_order,
    detect_outliers_iqr,
    detect_outliers_zscore,
    deterministic_signal_id,
    drawdown_series,
    easter_sunday,
    equity_from_returns,
    flatten_dict,
    getenv_bool,
    getenv_int,
    is_market_open,
    is_trading_day,
    max_drawdown,
    new_client_order_id,
    next_trading_day,
    ohlcv_from_provider,
    parse_datetime,
    previous_trading_day,
    profit_factor,
    read_json_file,
    resample_ohlcv,
    retry,
    safe_divide,
    session_bounds,
    sharpe_ratio,
    sortino_ratio,
    to_iso_z,
    to_utc,
    trading_days_between,
    us_market_holidays,
    utc_now,
    validate_ohlcv,
    winsorize_series,
    write_json_file,
)

UTC = timezone.utc


class TestDateTimeParsing:
    def test_utc_now_is_aware(self) -> None:
        assert utc_now().tzinfo is not None

    def test_to_utc_naive_assumed_utc(self) -> None:
        dt = to_utc(datetime(2024, 1, 2, 3, 4, 5))
        assert dt.tzinfo is not None and dt.utcoffset() == timedelta(0)

    def test_to_utc_rejects_non_datetime(self) -> None:
        with pytest.raises(TypeError):
            to_utc("2024-01-01")

    def test_parse_iso_string_with_z(self) -> None:
        dt = parse_datetime("2024-03-04T15:30:00Z")
        assert dt == datetime(2024, 3, 4, 15, 30, tzinfo=UTC)

    def test_parse_epoch_seconds_and_millis(self) -> None:
        assert parse_datetime(0) == datetime(1970, 1, 1, tzinfo=UTC)
        assert parse_datetime(0.0) == datetime(1970, 1, 1, tzinfo=UTC)
        millis = 1_700_000_000_000
        assert parse_datetime(millis) == datetime.fromtimestamp(millis / 1000, tz=UTC)

    def test_parse_date_object(self) -> None:
        assert parse_datetime(date(2024, 2, 3)) == datetime(2024, 2, 3, tzinfo=UTC)

    def test_parse_rejects_garbage(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            parse_datetime("not a datetime at all")

    def test_to_iso_z_roundtrip(self) -> None:
        stamp = to_iso_z(datetime(2024, 5, 6, 7, 8, 9, tzinfo=UTC))
        assert stamp.endswith("Z") and "+00:00" not in stamp
        assert parse_datetime(stamp) == datetime(2024, 5, 6, 7, 8, 9, tzinfo=UTC)

    def test_iso_z_lexicographic_order_matches_time(self) -> None:
        stamps = [to_iso_z(datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=h))
                  for h in range(0, 100, 7)]
        assert stamps == sorted(stamps)


class TestMarketCalendar:
    def test_easter_sunday_known_dates(self) -> None:
        assert easter_sunday(2024) == date(2024, 3, 31)
        assert easter_sunday(2025) == date(2025, 4, 20)
        assert easter_sunday(2026) == date(2026, 4, 5)

    def test_good_friday_is_holiday(self) -> None:
        # 2024 Good Friday was Mar 29
        assert date(2024, 3, 29) in us_market_holidays(2024)
        assert not is_trading_day(date(2024, 3, 29))

    def test_weekend_not_trading_day(self) -> None:
        assert not is_trading_day(date(2024, 6, 15))  # Saturday
        assert is_trading_day(date(2024, 6, 14))      # Friday

    def test_observed_holidays(self) -> None:
        # Christmas 2021 (Sat) observed Fri Dec 24
        assert date(2021, 12, 24) in us_market_holidays(2021)
        # July 4 2020 (Sat) observed Fri Jul 3
        assert date(2020, 7, 3) in us_market_holidays(2020)

    def test_juneteenth_from_2022(self) -> None:
        assert date(2022, 6, 20) in us_market_holidays(2022)  # Jun 19 was Sunday
        assert date(2021, 6, 18) not in us_market_holidays(2021)

    def test_thanksgiving_is_fourth_thursday(self) -> None:
        assert date(2024, 11, 28) in us_market_holidays(2024)

    def test_next_previous_trading_day(self) -> None:
        assert next_trading_day(date(2024, 3, 28)) == date(2024, 4, 1)  # skips Good Friday
        assert previous_trading_day(date(2024, 4, 1)) == date(2024, 3, 28)
        assert next_trading_day(date(2024, 4, 2), include=True) == date(2024, 4, 2)

    def test_add_trading_days(self) -> None:
        start = date(2024, 3, 27)
        assert add_trading_days(start, 1) == date(2024, 3, 28)
        assert add_trading_days(start, 2) == date(2024, 4, 1)   # Good Friday skipped
        assert add_trading_days(start, -1) == date(2024, 3, 26)

    def test_trading_days_between(self) -> None:
        # Mar 28 2024 (Thu) -> Apr 2 2024 (Tue): 28th, Apr 1, Apr 2 = 3
        assert trading_days_between(date(2024, 3, 28), date(2024, 4, 2)) == 3
        assert trading_days_between(date(2024, 4, 2), date(2024, 3, 28)) == 0

    def test_session_bounds_in_utc(self) -> None:
        open_utc, close_utc = session_bounds(date(2024, 7, 1))  # EDT (UTC-4)
        assert open_utc == datetime(2024, 7, 1, 13, 30, tzinfo=UTC)
        assert close_utc == datetime(2024, 7, 1, 20, 0, tzinfo=UTC)

    def test_session_bounds_dst_winter(self) -> None:
        open_utc, _ = session_bounds(date(2024, 1, 2))  # EST (UTC-5)
        assert open_utc == datetime(2024, 1, 2, 14, 30, tzinfo=UTC)

    def test_is_market_open(self) -> None:
        assert is_market_open(datetime(2024, 7, 1, 15, 0, tzinfo=UTC))   # 11:00 EDT
        assert not is_market_open(datetime(2024, 7, 1, 21, 0, tzinfo=UTC))  # after close
        assert not is_market_open(datetime(2024, 7, 4, 15, 0, tzinfo=UTC))  # holiday
        assert not is_market_open(datetime(2024, 7, 6, 15, 0, tzinfo=UTC))  # Saturday

    def test_anchor_keys(self) -> None:
        from utils.helpers import day_key, month_key, week_key
        moment = datetime(2024, 7, 1, 15, 0, tzinfo=UTC)
        assert day_key(moment) == "2024-07-01"
        assert week_key(moment) == "2024-W27"
        assert month_key(moment) == "2024-07"


class TestRetryAndRateLimiter:
    def test_retry_eventually_succeeds(self) -> None:
        calls = {"n": 0}

        @retry(attempts=3, base_delay=0.001, backoff=1.0)
        def flaky() -> str:
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("down")
            return "ok"

        assert flaky() == "ok"
        assert calls["n"] == 3

    def test_retry_gives_up(self) -> None:
        @retry(attempts=2, base_delay=0.001, backoff=1.0)
        def always_fails() -> None:
            raise ConnectionError("down")

        with pytest.raises(ConnectionError):
            always_fails()

    def test_retry_non_retryable_aborts_immediately(self) -> None:
        calls = {"n": 0}

        @retry(attempts=5, base_delay=0.001, give_up_on=(KeyError,))
        def bad() -> None:
            calls["n"] += 1
            raise KeyError("no retries for you")

        with pytest.raises(KeyError):
            bad()
        assert calls["n"] == 1

    def test_retry_rejects_invalid_attempts(self) -> None:
        with pytest.raises(ValueError):
            retry(attempts=0)

    def test_rate_limiter_spacing(self) -> None:
        limiter = RateLimiter(0.05)
        first = limiter.wait()
        second = limiter.wait()
        assert first < 0.03
        assert second >= 0.03


class TestIdsAndContainers:
    def test_order_id_format_and_uniqueness(self) -> None:
        a = new_client_order_id("aapl", "buy", strategy="meanrev")
        b = new_client_order_id("aapl", "buy", strategy="meanrev")
        assert a.startswith("FT-MEANREV-AAPL-BUY-")
        assert a != b

    def test_deterministic_signal_id(self) -> None:
        a = deterministic_signal_id("AAPL", "2024-01-01T00:00:00Z", "ensemble", "BUY")
        b = deterministic_signal_id("AAPL", "2024-01-01T00:00:00Z", "ensemble", "BUY")
        c = deterministic_signal_id("AAPL", "2024-01-02T00:00:00Z", "ensemble", "BUY")
        assert a == b and a != c

    def test_deep_merge(self) -> None:
        base = {"a": {"x": 1, "y": 2}, "b": 3}
        override = {"a": {"y": 20, "z": 30}, "c": 4}
        assert deep_merge(base, override) == {"a": {"x": 1, "y": 20, "z": 30}, "b": 3, "c": 4}
        assert base["a"]["y"] == 2  # inputs untouched

    def test_flatten_dict(self) -> None:
        assert flatten_dict({"a": {"b": {"c": 1}}, "d": 2}) == {"a.b.c": 1, "d": 2}

    def test_chunked(self) -> None:
        assert list(chunked([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]
        with pytest.raises(ValueError):
            list(chunked([1], 0))

    def test_dedupe_preserve_order(self) -> None:
        assert dedupe_preserve_order([3, 1, 3, 2, 1]) == [3, 1, 2]

    def test_json_roundtrip_atomic(self, tmp_path) -> None:
        target = tmp_path / "nested" / "data.json"
        write_json_file(target, {"when": datetime(2024, 1, 1, tzinfo=UTC), "n": np.int64(3)})
        loaded = read_json_file(target)
        assert loaded == {"n": 3, "when": "2024-01-01T00:00:00.000000Z"}

    def test_read_json_missing_returns_default(self, tmp_path) -> None:
        assert read_json_file(tmp_path / "absent.json", default={}) == {}


class TestFinanceMath:
    def test_clamp_and_safe_divide(self) -> None:
        assert clamp(5.0, 0.0, 3.0) == 3.0
        assert safe_divide(1.0, 0.0, default=-1.0) == -1.0
        assert safe_divide(4.0, 2.0) == 2.0
        with pytest.raises(ValueError):
            clamp(1.0, 2.0, 1.0)

    def test_drawdown_series_and_current(self) -> None:
        equity = pd.Series([100.0, 110.0, 99.0, 120.0])
        dd = drawdown_series(equity)
        assert dd.iloc[0] == 0.0
        assert dd.iloc[2] == pytest.approx(99 / 110 - 1)
        assert current_drawdown(99.0, 110.0) == pytest.approx(99 / 110 - 1)
        assert current_drawdown(100.0, 0.0) == 0.0

    def test_max_drawdown_with_recovery(self) -> None:
        equity = pd.Series(
            [100.0, 80.0, 95.0, 105.0],
            index=pd.date_range("2024-01-01", periods=4, freq="D", tz="UTC"),
        )
        mdd, trough, recovery = max_drawdown(equity)
        assert mdd == pytest.approx(-0.20)
        assert trough == pd.Timestamp("2024-01-02", tz="UTC")
        assert recovery == pd.Timestamp("2024-01-04", tz="UTC")

    def test_max_drawdown_no_recovery(self) -> None:
        equity = pd.Series([100.0, 90.0])
        mdd, _, recovery = max_drawdown(equity)
        assert mdd == pytest.approx(-0.10)
        assert recovery is None

    def test_equity_from_returns_and_sharpe(self) -> None:
        rets = pd.Series([0.01, -0.005, 0.02, 0.0])
        eq = equity_from_returns(rets, initial=1000.0)
        assert eq.iloc[-1] == pytest.approx(1000 * 1.01 * 0.995 * 1.02)
        assert sharpe_ratio(pd.Series(dtype=float)) == 0.0
        assert sharpe_ratio(pd.Series([0.001] * 100)) == 0.0  # zero variance
        positive = sharpe_ratio(pd.Series(np.random.default_rng(7).normal(0.002, 0.01, 500)))
        assert positive > 0

    def test_sortino_and_calmar(self) -> None:
        rets = pd.Series(np.random.default_rng(11).normal(0.001, 0.01, 300))
        assert sortino_ratio(rets) != 0.0
        equity = pd.Series([100.0, 102.0, 90.0, 95.0, 110.0])
        assert calmar_ratio(equity) != 0.0

    def test_profit_factor(self) -> None:
        assert profit_factor([10.0, -5.0, 15.0, -5.0]) == pytest.approx(2.5)
        assert profit_factor([1.0, 2.0]) == float("inf")
        assert profit_factor([]) == 0.0


class TestStatistics:
    def test_iqr_outliers(self) -> None:
        s = pd.Series(list(np.arange(50, dtype=float)) + [1000.0])
        mask = detect_outliers_iqr(s, k=1.5)
        assert mask.iloc[-1] and not mask.iloc[0]

    def test_iqr_constant_series_safe(self) -> None:
        mask = detect_outliers_iqr(pd.Series([1.0] * 50))
        assert not mask.any()

    def test_zscore_outliers_use_mad(self) -> None:
        rng = np.random.default_rng(3)
        rets = pd.Series(rng.normal(0, 0.01, 500))
        rets.iloc[42] = 0.9  # extreme spike
        mask = detect_outliers_zscore(rets, threshold=8.0)
        assert mask.sum() >= 1
        assert mask.iloc[42]

    def test_zscore_constant_series_safe(self) -> None:
        mask = detect_outliers_zscore(pd.Series([5.0] * 10))
        assert not mask.any()

    def test_winsorize(self) -> None:
        s = pd.Series(range(100), dtype=float)
        clipped = winsorize_series(s, (0.05, 0.05))
        assert clipped.min() == pytest.approx(5.0, abs=1.0)
        assert clipped.max() == pytest.approx(94.0, abs=1.0)


class TestOhlcv:
    def _frame(self) -> pd.DataFrame:
        return pd.DataFrame({
            "timestamp": pd.date_range("2024-01-02", periods=4, freq="h", tz="UTC"),
            "open": [100, 101, 102, 103],
            "high": [101, 102, 103, 104],
            "low": [99, 100, 101, 102],
            "close": [100.5, 101.5, 102.5, 103.5],
            "volume": [1000, 1100, 1200, 1300],
        })

    def test_validate_clean_frame(self) -> None:
        df, issues = validate_ohlcv(self._frame())
        assert len(df) == 4
        assert issues == []

    def test_validate_drops_duplicates_and_sorts(self) -> None:
        df = self._frame()
        df = pd.concat([df, df.iloc[[2]]], ignore_index=True)
        df = df.sample(frac=1.0, random_state=1)
        cleaned, issues = validate_ohlcv(df)
        assert len(cleaned) == 4
        assert cleaned["timestamp"].is_monotonic_increasing
        assert any("duplicate" in i for i in issues)

    def test_validate_flags_ohlc_inconsistency(self) -> None:
        df = self._frame()
        df.loc[1, "high"] = 95  # below open/close
        _, issues = validate_ohlcv(df)
        assert any("consistency" in i for i in issues)

    def test_validate_drops_nonpositive_prices(self) -> None:
        df = self._frame()
        df.loc[0, "close"] = 0
        cleaned, issues = validate_ohlcv(df)
        assert len(cleaned) == 3
        assert any("non-positive" in i for i in issues)

    def test_validate_missing_columns(self) -> None:
        frame = pd.DataFrame({"timestamp": [datetime(2024, 1, 2, tzinfo=UTC)],
                              "open": [100.0]})
        cleaned, issues = validate_ohlcv(frame)
        assert cleaned.empty
        assert any("missing columns" in i for i in issues)

    def test_validate_empty_frame(self) -> None:
        cleaned, issues = validate_ohlcv(pd.DataFrame())
        assert cleaned.empty
        assert any("empty" in i for i in issues)

    def test_ohlcv_from_provider_flat(self) -> None:
        raw = pd.DataFrame(
            {"Open": [1.0], "High": [2.0], "Low": [0.5], "Close": [1.5],
             "Adj Close": [1.4], "Volume": [100]},
            index=pd.DatetimeIndex(["2024-01-02 09:30"], tz="US/Eastern"),
        )
        out = ohlcv_from_provider(raw)
        assert list(out.columns)[:3] == ["timestamp", "open", "high"]
        assert out.iloc[0]["adj_close"] == pytest.approx(1.4)
        assert str(out.iloc[0]["timestamp"].tz) in ("UTC", "datetime.timezone.utc")

    def test_ohlcv_from_provider_multiindex(self) -> None:
        cols = pd.MultiIndex.from_product([["Open", "High", "Low", "Close", "Volume"],
                                           ["AAPL"]])
        raw = pd.DataFrame(
            [[1.0, 2.0, 0.5, 1.5, 100]], columns=cols,
            index=pd.DatetimeIndex(["2024-01-02"], tz="UTC"),
        )
        out = ohlcv_from_provider(raw)
        assert out.iloc[0]["close"] == pytest.approx(1.5)

    def test_resample_to_4h(self) -> None:
        hours = pd.DataFrame({
            "timestamp": pd.date_range("2024-01-02 09:30", periods=8, freq="h", tz="UTC"),
            "open": np.arange(8, dtype=float) + 100,
            "high": np.arange(8, dtype=float) + 100.5,
            "low": np.arange(8, dtype=float) + 99.5,
            "close": np.arange(8, dtype=float) + 100.25,
            "volume": [100] * 8,
        })
        out = resample_ohlcv(hours, "4h")
        assert len(out) == 2
        assert out.iloc[0]["open"] == 100.0
        assert out.iloc[0]["volume"] == 400
        assert out.iloc[1]["close"] == pytest.approx(107.25)


class TestEnvCoercion:
    def test_getenv_helpers(self, monkeypatch) -> None:
        monkeypatch.setenv("FT_I", "42")
        monkeypatch.setenv("FT_B", "yes")
        monkeypatch.setenv("FT_BAD", "not-a-number")
        assert getenv_int("FT_I") == 42
        assert getenv_bool("FT_B") is True
        assert getenv_int("FT_BAD", default=7) == 7
        assert getenv_int("FT_ABSENT", default=3) == 3
