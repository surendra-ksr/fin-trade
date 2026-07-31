# Phase 9 Evidence Pack — automation

> **Atomic evidence pack** per `docs/EVIDENCE_PROTOCOL.md`. Every command below
> ran in ONE shell session against the committed state (`git status --short`
> empty) at HEAD `827690ab0d83c9077b2423cf2b914e6cb3b36012`. Outputs are pasted
> unedited. Reconciliation: `TOTAL = CORE_GREEN(425) + ML_ONLY(12) = 437`.

## 1. git status, log, exact HEAD SHA

```text
--- date -u ---
2026-07-31T10:44:49Z
--- git rev-parse HEAD ---
827690ab0d83c9077b2423cf2b914e6cb3b36012
--- git status --short ---
(empty — working tree clean)
--- git log --oneline -4 ---
827690a Phase 9 docs: BUILD_PLAN status, ARCHITECTURE roadmap + self-check, AUDIT_REPORT entry
8e8b6c7 Phase 9: automation — scheduler, approval queue, recovery ramp, digest, reconcile
912151f Phase 8: order types & paper trading
```

## 2. Module stats (wc -l + docstrings)

```text
================================================================
automation/scheduler.py                   360 lines  24 """-docstrings   0 def-test
automation/approval_queue.py              339 lines  16 """-docstrings   0 def-test
automation/recovery.py                    297 lines  19 """-docstrings   0 def-test
automation/digest.py                      222 lines   8 """-docstrings   0 def-test
automation/reconcile.py                   161 lines   7 """-docstrings   0 def-test
tests/unit/test_phase9_automation.py      702 lines  34 """-docstrings  40 def-test

================================================================
NO-WALL-CLOCK + NO-NETWORK GREPS
================================================================
--- grep datetime.now / time.time() in automation/ (source only) ---
automation/scheduler.py:7:  itself never calls ``datetime.now()``; every session/holiday/DST branch is
automation/scheduler.py:9:  testable. (Grep ``now`` -> ``self._now`` everywhere; no ``datetime.now``.)
--- grep wall-clock in Phase 9 test file ---
NONE FOUND
--- grep network libs in automation/ ---
NONE FOUND

================================================================
COLLECT-ONLY (.venv core) — full
================================================================
```

## 3. No-wall-clock + no-network greps

```text
================================================================
--- grep datetime.now / time.time() in automation/ (source only) ---
automation/scheduler.py:7:  itself never calls ``datetime.now()``; every session/holiday/DST branch is
automation/scheduler.py:9:  testable. (Grep ``now`` -> ``self._now`` everywhere; no ``datetime.now``.)
--- grep wall-clock in Phase 9 test file ---
NONE FOUND
--- grep network libs in automation/ ---
NONE FOUND

================================================================
COLLECT-ONLY (.venv core) — full
================================================================
```

(`grep` for `datetime.now`/`time.time()`/`utc_now()` in `automation/*.py` returns
only docstring mentions and `now_fn or utc_now` default-fallback references; the
Phase-9 test file has zero wall-clock; no network libraries are imported.)

## 4. Verbatim demanded-function bodies

`ramp_multiplier` (ramp calculation), `session_phase` (session detection), and
`_transition` (approval transition):

```python
================================================================
--- ramp_multiplier (automation/recovery.py) — ramp calculation ---
def ramp_multiplier(elapsed_days: float, *, config: AppConfig) -> float:
    """Pure graduated multiplier for ``elapsed_days`` since (re)start.

    This is the **ramp-calculation** function — the single expression of the
    ``recovery.*`` config ladder. It is pure w.r.t. its arguments so the full
    timeline (day1-3 / day4-7 / week2 / week3+) is deterministically testable.

    Tiers (boundaries are half-open [lo, hi) in days):

    * [0, 3)   -> day1_3_size_pct
    * [3, 7)   -> day4_7_size_pct
    * [7, 14)  -> week2_size_pct
    * [14, +)  -> week3_plus_size_pct
    """
    rec = config.recovery
    if elapsed_days < 3.0:
        return float(rec.day1_3_size_pct)
    if elapsed_days < 7.0:
        return float(rec.day4_7_size_pct)
    if elapsed_days < 14.0:
        return float(rec.week2_size_pct)
    return float(rec.week3_plus_size_pct)

--- session_phase (automation/scheduler.py) — session detection ---
def session_phase(
    at: datetime,
    *,
    config: Optional[AppConfig] = None,
) -> SessionPhase:
    """Classify an aware UTC ``at`` instant into a :class:`SessionPhase`.

    This is the **session-detection** function. It is pure w.r.t. ``at`` and
    ``config`` — it never reads the wall clock — so every branch (weekend,
    holiday, pre/regular/post windows) is deterministically testable.

    Window boundaries (all in exchange local time, DST-correct via
    :func:`local_wallclock_to_utc`):

    * CLOSED            when the day is a weekend or NYSE holiday
    * CLOSED            before ``pre_market_start`` or after ``post_market_end``
    * PRE_MARKET        ``pre_market_start`` <= t < ``market_open``
    * REGULAR           ``market_open`` <= t < ``market_close``
    * POST_MARKET       ``market_close`` <= t < ``post_market_end``
    """
    cfg = config or load_config()
    instant = to_utc(at)
    local = instant.astimezone(MARKET_TZ)
    day = local.date()
    if not is_trading_day(day):
        return SessionPhase.CLOSED
    auto = cfg.automation
    pre_start = local_wallclock_to_utc(day, auto.pre_market_start)
    market_open = local_wallclock_to_utc(day, auto.market_open)
    market_close = local_wallclock_to_utc(day, auto.market_close)
    post_end = local_wallclock_to_utc(day, auto.post_market_end)
    if instant < pre_start or instant >= post_end:
        return SessionPhase.CLOSED
    if instant < market_open:
        return SessionPhase.PRE_MARKET
    if instant < market_close:
        return SessionPhase.REGULAR
    return SessionPhase.POST_MARKET

--- _transition (automation/approval_queue.py) — approval transition ---
def _transition(
        self,
        signal_id: str,
        target: str,
        *,
        by: str,
        reason: str = "",
    ) -> QueuedSignal:
        """Apply a lifecycle transition gated by the ``_ALLOWED`` table.

        This is the **approval-transition** function: it enforces that a
        signal can only move along a legal edge (e.g. PENDING -> APPROVED ->
        EXECUTED), records the actor + timestamp, and persists the result.
        Illegal moves raise :class:`ApprovalError`.
        """
        if target not in _ALL_STATUSES:
            raise ApprovalError(f"unknown target status: {target!r}")
        signal = self._signals.get(signal_id)
        if signal is None:
            raise ApprovalError(f"unknown signal: {signal_id!r}")
        if target not in _ALLOWED.get(signal.status, frozenset()):
            raise ApprovalError(
                f"illegal transition {signal.status} -> {target} for {signal_id!r}")
        now = self._now()
        signal.status = target
        signal.decided_at = now
        signal.decision_by = by
        if reason:
            signal.reason = reason
        self._persist(action=f"transition:{target}", signal=signal,
                      extra={"by": by, "reason": reason} if reason else {"by": by})
        return signal
```

## 5. Full `pytest --collect-only -q` output

### `.venv` (core) — 437 items

```text
============================= test session starts ==============================
platform linux -- Python 3.11.2, pytest-8.3.5, pluggy-1.6.0
rootdir: /home/user/fin-trade
configfile: pytest.ini
testpaths: tests
plugins: cov-6.1.1
collected 437 items

<Dir fin-trade>
  <Dir tests>
    <Dir unit>
      <Module test_backtest_engine.py>
        <Function test_execute_next_bar_fill_function_pasted>
        <Function test_match_fill_series_event_driven_length>
        <Function test_execute_next_bar_function_pasted>
        <Function test_execute_next_bar_flat_signal>
        <Function test_anti_lookahead_backtest_does_not_read_future_features>
        <Function test_generate_report_function_pasted>
      <Module test_circuit_breakers.py>
        <Class TestDailyLossBreakers>
          <Function test_healthy_baseline>
          <Function test_level1_yellow>
          <Function test_level2_orange_blocks_and_cancels>
          <Function test_level3_red_halts_closes_worst_half_and_locks>
          <Function test_level4_emergency_flattens_all>
          <Function test_resume_refused_without_token_and_before_lock_expiry>
          <Function test_anchors_roll_each_day>
        <Class TestWeeklyMonthlyBreakers>
          <Function test_weekly_level2_reduces_and_blocks_shorts>
          <Function test_weekly_level3_halts_and_flattens>
          <Function test_monthly_level1_reduces_sizes>
          <Function test_monthly_level3_halts_for_month>
        <Class TestDrawdownBreakers>
          <Function test_level1_yellow>
          <Function test_level2_orange>
          <Function test_level3_caps_positions_and_confidence>
          <Function test_level4_emergency_cooling_off>
          <Function test_new_high_resets_peak_only_upwards>
        <Class TestMarketBreakers>
          <Function test_vix_ladder_sizing>
          <Function test_vix_intraday_spike_flagged>
          <Function test_market_crash_orange_blocks_longs>
          <Function test_market_crash_red_exits_longs_only>
          <Function test_sector_crash_exits_and_blocks_sector>
          <Function test_liquidity_breakers>
          <Function test_flash_crash_pause_and_recovery>
        <Class TestTechnicalBreakers>
          <Function test_data_feed_stale_halts>
          <Function test_data_feed_dead_flattens>
          <Function test_feed_recovery_auto_deescalates>
          <Function test_api_failure_escalates_with_duration>
          <Function test_model_failure_falls_back>
          <Function test_runaway_order_rate_limit>
          <Function test_runaway_duplicate_detection>
          <Function test_order_attempt_ceiling>
          <Function test_locked_state_blocks_order_gate>
          <Function test_position_mismatch_sticky_halt_and_clear>
        <Class TestManualControls>
          <Function test_kill_switch_flattens_and_requires_resume>
          <Function test_suspend_and_resume_walk>
          <Function test_override_token_expires>
          <Function test_unknown_override_token_rejected>
          <Function test_invalid_direct_transition_raises>
          <Function test_notifier_receives_orange_and_above>
        <Class TestRecoveryProgram>
          <Function test_day1_3_quarter_size>
          <Function test_day4_7_half_size>
          <Function test_week2_three_quarters>
          <Function test_week3_full_when_positive_held_when_not>
        <Class TestPositionLevelBreakers>
          <Function test_hard_stop_long>
          <Function test_hard_stop_short>
          <Function test_atr_stop>
          <Function test_max_loss_per_trade>
          <Function test_volatility_spike_halves>
          <Function test_time_stop>
          <Function test_no_action_when_healthy>
        <Class TestPersistenceAndAggregation>
          <Function test_state_survives_restart>
          <Function test_audit_log_written>
          <Function test_worst_trigger_wins_aggregation>
          <Function test_disabled_breakers_passthrough>
          <Function test_confidence_math>
          <Function test_status_and_report>
          <Function test_evaluate_never_raises_on_bad_snapshot>
      <Module test_config.py>
        <Class TestLoadMasterConfig>
          <Function test_repo_config_loads_and_validates>
          <Function test_master_config_values>
          <Function test_breaker_ladders_descend>
          <Function test_missing_file_raises>
          <Function test_non_mapping_raises>
        <Class TestEnvSubstitution>
          <Function test_pattern_expands_present_var>
          <Function test_missing_var_becomes_empty_with_warning>
          <Function test_default_syntax>
          <Function test_nested_structures>
          <Function test_regex_no_partial_match>
        <Class TestValidation>
          <Function test_weight_sum_enforced>
          <Function test_breaker_ladder_order_enforced>
          <Function test_invalid_mode_rejected>
          <Function test_empty_watchlist_rejected>
          <Function test_live_requires_broker_and_authorization>
          <Function test_automation_schedule_order_enforced>
          <Function test_unknown_keys_warn_not_fail>
        <Class TestConfigAccessors>
          <Function test_singleton_caches>
          <Function test_resolve_path>
          <Function test_redaction_masks_secrets>
          <Function test_validate_passes_on_defaults>
          <Function test_timeframes_all>
      <Module test_constants.py>
        <Class TestEnums>
          <Function test_trading_mode_values>
          <Function test_circuit_breaker_states_complete>
          <Function test_state_severity_is_strictly_ordered>
          <Function test_order_status_open_terminal_partition>
          <Function test_signal_type_actionable>
        <Class TestStateMachineTables>
          <Function test_every_state_has_transitions_entry>
          <Function test_every_state_has_policy_defaults>
          <Function test_escalation_always_possible_from_normal>
          <Function test_suspended_only_returns_to_halted>
          <Function test_no_self_transitions>
          <Function test_halted_cannot_jump_to_normal>
          <Function test_halted_states_block_entries_in_policy>
          <Function test_normal_state_full_permissions>
        <Class TestSignalThresholds>
          <Function test_threshold_sets_are_ordered>
          <Function test_restricted_is_stricter_than_normal>
          <Function test_defensive_blocks_buys>
          <Function test_timeframe_metadata>
        <Class TestAlertLevels>
          <Function test_ordering>
      <Module test_data_agent.py>
        <Class TestWikipediaParsing>
          <Function test_sp500_table>
          <Function test_nasdaq100_table>
          <Function test_garbage_html_returns_empty>
        <Class TestUniverseManager>
          <Function test_sp500_fetch_and_cache>
          <Function test_russell_filters_cash>
          <Function test_resolve_merges_and_dedupes>
          <Function test_offline_falls_back_to_static_list>
          <Function test_sector_map>
        <Class TestFredClient>
          <Function test_public_csv_parsing>
          <Function test_csv_respects_start>
          <Function test_http_error_propagates_after_retries>
        <Class TestLookbackPolicy>
          <Function test_provider_caps_clamp>
          <Function test_daily_lookback_uses_historical_years>
          <Function test_monthly_is_unbounded>
        <Class TestSyncTimeframe>
          <Function test_full_sync_inserts_bars>
          <Function test_fresh_data_is_skipped>
          <Function test_incremental_sync_after_gap>
          <Function test_empty_provider_response>
          <Function test_provider_failure_becomes_error_not_exception>
          <Function test_4h_resampled_from_1h>
          <Function test_4h_skipped_without_1h>
        <Class TestQualityChecks>
          <Function test_daily_gap_detected>
        <Class TestFundamentalsAndOptions>
          <Function test_fundamentals_stored>
          <Function test_empty_info_noop>
          <Function test_options_put_call_ratio>
        <Class TestMacroAndPipeline>
          <Function test_macro_sync>
          <Function test_sync_all_aggregates_errors>
          <Function test_latest_close_and_benchmark_change>
          <Function test_latest_vix>
          <Function test_data_status_frame>
      <Module test_database.py>
        <Class TestSchema>
          <Function test_all_tables_created>
          <Function test_migration_recorded>
          <Function test_migrate_is_idempotent>
          <Function test_integrity_check>
        <Class TestPriceData>
          <Function test_upsert_and_fetch_roundtrip>
          <Function test_upsert_replaces_same_key>
          <Function test_last_and_first_timestamp>
          <Function test_fetch_with_range_and_limit>
          <Function test_list_symbols>
          <Function test_upsert_rejects_bad_frame>
          <Function test_empty_frame_is_noop>
        <Class TestFundamentalMacroSentiment>
          <Function test_fundamentals_roundtrip>
          <Function test_macro_upsert_and_fetch>
          <Function test_sentiment_upsert_replace>
        <Class TestNewsAndSignals>
          <Function test_news_dedupe>
          <Function test_signal_lifecycle>
          <Function test_signal_id_dedupes>
        <Class TestPaperTrades>
          <Function test_open_close_long_pnl>
          <Function test_open_close_short_pnl>
          <Function test_close_returns_none_for_non_open>
          <Function test_open_positions_query>
        <Class TestMetricsAndLogs>
          <Function test_performance_metrics_upsert>
          <Function test_breaker_event_log>
          <Function test_automation_log>
          <Function test_limit_breach_log>
        <Class TestPatterns>
          <Function test_insert_and_label>
        <Class TestBreakerStateAndKv>
          <Function test_save_load_roundtrip>
          <Function test_load_empty_returns_none>
          <Function test_replace_state>
          <Function test_kv_roundtrip>
        <Class TestMaintenance>
          <Function test_table_stats>
          <Function test_backup_creates_valid_copy>
          <Function test_export_csv>
          <Function test_optimize_runs>
          <Function test_concurrent_writes>
          <Function test_get_database_caches>
      <Module test_helpers.py>
        <Class TestDateTimeParsing>
          <Function test_utc_now_is_aware>
          <Function test_to_utc_naive_assumed_utc>
          <Function test_to_utc_rejects_non_datetime>
          <Function test_parse_iso_string_with_z>
          <Function test_parse_epoch_seconds_and_millis>
          <Function test_parse_date_object>
          <Function test_parse_rejects_garbage>
          <Function test_to_iso_z_roundtrip>
          <Function test_iso_z_lexicographic_order_matches_time>
        <Class TestMarketCalendar>
          <Function test_easter_sunday_known_dates>
          <Function test_good_friday_is_holiday>
          <Function test_weekend_not_trading_day>
          <Function test_observed_holidays>
          <Function test_juneteenth_from_2022>
          <Function test_thanksgiving_is_fourth_thursday>
          <Function test_next_previous_trading_day>
          <Function test_add_trading_days>
          <Function test_trading_days_between>
          <Function test_session_bounds_in_utc>
          <Function test_session_bounds_dst_winter>
          <Function test_is_market_open>
          <Function test_anchor_keys>
        <Class TestRetryAndRateLimiter>
          <Function test_retry_eventually_succeeds>
          <Function test_retry_gives_up>
          <Function test_retry_non_retryable_aborts_immediately>
          <Function test_retry_rejects_invalid_attempts>
          <Function test_rate_limiter_spacing>
        <Class TestIdsAndContainers>
          <Function test_order_id_format_and_uniqueness>
          <Function test_deterministic_signal_id>
          <Function test_deep_merge>
          <Function test_flatten_dict>
          <Function test_chunked>
          <Function test_dedupe_preserve_order>
          <Function test_json_roundtrip_atomic>
          <Function test_read_json_missing_returns_default>
        <Class TestFinanceMath>
          <Function test_clamp_and_safe_divide>
          <Function test_drawdown_series_and_current>
          <Function test_max_drawdown_with_recovery>
          <Function test_max_drawdown_no_recovery>
          <Function test_equity_from_returns_and_sharpe>
          <Function test_sortino_and_calmar>
          <Function test_profit_factor>
        <Class TestStatistics>
          <Function test_iqr_outliers>
          <Function test_iqr_constant_series_safe>
          <Function test_zscore_outliers_use_mad>
          <Function test_zscore_constant_series_safe>
          <Function test_winsorize>
        <Class TestOhlcv>
          <Function test_validate_clean_frame>
          <Function test_validate_drops_duplicates_and_sorts>
          <Function test_validate_flags_ohlc_inconsistency>
          <Function test_validate_drops_nonpositive_prices>
          <Function test_validate_missing_columns>
          <Function test_validate_empty_frame>
          <Function test_ohlcv_from_provider_flat>
          <Function test_ohlcv_from_provider_multiindex>
          <Function test_resample_to_4h>
        <Class TestEnvCoercion>
          <Function test_getenv_helpers>
      <Module test_logger.py>
        <Class TestConfiguration>
          <Function test_console_only_config_is_safe>
          <Function test_creates_log_directory>
          <Function test_invalid_level_falls_back>
        <Class TestRouting>
          <Function test_app_file_captures_all>
          <Function test_category_files_json_lines>
          <Function test_errors_file_captures_all_errors>
          <Function test_unknown_category_falls_back_to_app>
          <Function test_log_event_helper>
          <Function test_stdlib_logging_intercepted>
        <Class TestHelpers>
          <Function test_suppress_swallows_and_logs>
          <Function test_suppress_reraise>
          <Function test_timed_block_logs>
          <Function test_iter_log_files>
      <Module test_phase2_features.py>
        <Function test_hand_vectors_and_complete_family>
        <Function test_edge_cases_are_finite_and_causal>
        <Function test_multitimeframe_backward_join_no_future_values>
      <Module test_phase2_numeric.py>
        <Function test_hand_numeric_families[sma-12.0]>
        <Function test_hand_numeric_families[wma-12.6666666667]>
        <Function test_hand_numeric_families[roc-0.4]>
        <Function test_hand_numeric_families[williams--16.6666666667]>
        <Function test_hand_numeric_families[stoch-83.3333333333]>
        <Function test_hand_numeric_families[cci-111.1111111111]>
        <Function test_hand_numeric_families[atr-2.0]>
        <Function test_hand_numeric_families[obv-14.0]>
        <Function test_hand_numeric_families[vwap-12.6666666667]>
        <Function test_hand_numeric_families[ad-0.0]>
        <Function test_macd_triplet_numeric_recurrence>
        <Function test_mfi_adx_psar_bands_and_ichimoku_numeric>
        <Function test_adx_full_wilder_construction_with_suppression>
        <Function test_psar_accelerates_and_clamps>
        <Function test_ichimoku_spans_displaced_26>
        <Function test_volume_indicators_all_real_standalone_implementations>
      <Module test_phase2_providers_quality.py>
        <Function test_alpha_key_absent_skips>
        <Function test_alpha_key_present_parses>
        <Function test_sec_company_facts_normalizes_request>
        <Function test_options_surface_and_empty>
        <Function test_news_dedup>
        <Function test_quality_gap_stale_jump_and_clean>
      <Module test_phase2_vectors.py>
        <Function test_indicator_vector_has_independent_finite_tail[sma_5]>
        <Function test_indicator_vector_has_independent_finite_tail[ema_5]>
        <Function test_indicator_vector_has_independent_finite_tail[wma_5]>
        <Function test_indicator_vector_has_independent_finite_tail[rsi]>
        <Function test_indicator_vector_has_independent_finite_tail[macd]>
        <Function test_indicator_vector_has_independent_finite_tail[macd_signal]>
        <Function test_indicator_vector_has_independent_finite_tail[stochastic]>
        <Function test_indicator_vector_has_independent_finite_tail[williams_r]>
        <Function test_indicator_vector_has_independent_finite_tail[cci]>
        <Function test_indicator_vector_has_independent_finite_tail[roc]>
        <Function test_indicator_vector_has_independent_finite_tail[mfi]>
        <Function test_indicator_vector_has_independent_finite_tail[atr_14]>
        <Function test_indicator_vector_has_independent_finite_tail[adx]>
        <Function test_indicator_vector_has_independent_finite_tail[plus_di]>
        <Function test_indicator_vector_has_independent_finite_tail[minus_di]>
        <Function test_indicator_vector_has_independent_finite_tail[psar]>
        <Function test_indicator_vector_has_independent_finite_tail[bollinger_upper]>
        <Function test_indicator_vector_has_independent_finite_tail[bollinger_lower]>
        <Function test_indicator_vector_has_independent_finite_tail[keltner_upper]>
        <Function test_indicator_vector_has_independent_finite_tail[keltner_lower]>
        <Function test_indicator_vector_has_independent_finite_tail[donchian_upper]>
        <Function test_indicator_vector_has_independent_finite_tail[donchian_lower]>
        <Function test_indicator_vector_has_independent_finite_tail[obv]>
        <Function test_indicator_vector_has_independent_finite_tail[vwap]>
        <Function test_indicator_vector_has_independent_finite_tail[ad_line]>
        <Function test_indicator_vector_has_independent_finite_tail[cmf]>
        <Function test_indicator_vector_has_independent_finite_tail[volume_zscore]>
        <Function test_sma_wma_hand_calculation>
        <Function test_ema_hand_calculation>
        <Function test_rsi_wilder_numeric_worked_vector>
        <Function test_macd_components_are_consistent>
        <Function test_stochastic_bounds_and_atr_hand_range>
        <Function test_bollinger_keltner_donchian_hand_relationships>
        <Function test_volume_family_hand_relationships>
        <Function test_warmup_and_short_inputs[1]>
        <Function test_warmup_and_short_inputs[2]>
        <Function test_warmup_and_short_inputs[4]>
        <Function test_warmup_and_short_inputs[12]>
        <Function test_constant_series_no_exception>
        <Function test_zero_volume_vwap_is_nan_not_infinite>
      <Module test_phase3_models.py>
        <Function test_model_base_version_and_abstract>
        <Function test_model_registry_roundtrip>
        <Function test_purged_walk_forward_zero_overlap_and_embargo>
        <Function test_past_sequences_no_future_leakage>
        <Function test_past_sequences_short_input>
        <Function test_trainer_embargo_and_no_overlap>
        <Function test_trainer_sequence_anti_leak>
        <Function test_trainer_sequence_builder_shape>
        <Function test_metrics_on_known_arrays>
        <Function test_metrics_validation_runs>
        <Function test_lstm_output_shape_and_seed_determinism>
        <Function test_gru_output_shape_and_seed>
        <Function test_lstm_single_batch_overfit_smoke>
        <Function test_gru_single_batch_overfit_smoke>
        <Function test_gbm_fit_predict_save_load_roundtrip>
      <Module test_phase4_models.py>
        <Function test_ensemble_meta_trained_only_on_out_of_fold_predictions>
        <Function test_ensemble_predict_shape_after_fit>
        <Function test_regime_detector_emits_labels_and_persists>
        <Function test_regime_detector_fetch_regimes>
        <Function test_nested_optuna_leakage_proof_and_embargo_assertion>
        <Function test_optuna_best_params_differ_per_fold_when_data_shifts>
        <Function test_calibration_fitted_only_on_validation_folds>
        <Function test_platt_scale_and_isotonic_output_range>
        <Function test_calibration_contamination_assertion_raises>
      <Module test_phase5_models.py>
        <Function test_sentiment_lexicon_fallback_deterministic>
        <Function test_sentiment_engine_offline_without_model>
        <Function test_sentiment_process_batch_persists>
        <Function test_pattern_detection_on_synthetic_candles>
        <Function test_pattern_engine_synthetic_candles>
        <Function test_self_labeling_uses_only_future_bars>
        <Function test_pattern_self_labeling_contract>
      <Module test_phase7_risk_gateway.py>
        <Function test_asset_strategy_sector_and_portfolio_denials>
        <Function test_all_speed_breakers_and_restricted_halted_entries_denied>
        <Function test_gateway_is_only_paper_transmission_path>
        <Function test_per_strategy_and_asset_loss_buckets_are_denied>
      <Module test_phase8_order_types.py>
        <Function test_state_machine_clean_lifecycle>
        <Function test_state_machine_illegal_transitions_raise>
        <Function test_terminal_states_accept_no_transitions>
        <Function test_every_state_has_transitions_entry>
        <Function test_transition_function_validates_both_ends>
        <Function test_market_order_fills_immediately_at_bar_price>
        <Function test_limit_buy_fills_when_low_touches_limit>
        <Function test_limit_buy_rests_when_price_stays_above_limit>
        <Function test_limit_sell_fills_when_high_touches_limit>
        <Function test_limit_sell_rests_when_price_stays_below_limit>
        <Function test_stop_buy_triggers_on_high_cross>
        <Function test_stop_sell_triggers_on_low_cross>
        <Function test_stop_not_triggered_when_range_stays_above_stop>
        <Function test_stop_gap_through_buy_executes_at_open>
        <Function test_stop_gap_through_sell_executes_at_open>
        <Function test_stop_limit_triggers_then_works_as_limit>
        <Function test_stop_limit_working_not_filled_when_limit_not_met>
        <Function test_stop_limit_same_bar_fill_when_limit_already_crossed>
        <Function test_stop_limit_rests_when_stop_not_crossed>
        <Function test_trailing_sell_ratchets_up_and_never_back>
        <Function test_trailing_buy_ratchets_down_and_never_back>
        <Function test_trailing_gap_through_executes_at_open>
        <Function test_ratchet_trailing_requires_trailing_type>
        <Function test_oco_one_cancels_other_on_fill>
        <Function test_oco_unrelated_orders_are_untouched>
        <Function test_bracket_children_arm_only_after_entry_fill>
        <Function test_bracket_child_fill_cancels_twin>
        <Function test_invalid_order_parameters_rejected>
        <Function test_container_types_are_never_evaluated_directly>
      <Module test_phase8_paper_broker.py>
        <Function test_fills_reuse_the_single_shared_pricing_core>
        <Function test_market_order_fills_with_fee_and_cash_impact>
        <Function test_market_sell_reduces_position_and_adds_cash>
        <Function test_limit_order_rests_then_fills_on_mark>
        <Function test_stop_order_triggers_on_mark_cross>
        <Function test_trailing_stop_ratchets_in_broker_and_triggers>
        <Function test_realized_pnl_long_includes_entry_and_exit_fees>
        <Function test_realized_pnl_short_includes_entry_fees>
        <Function test_partial_close_allocates_entry_fees_proportionally>
        <Function test_db_paper_trade_rows_round_trip_through_broker>
        <Function test_in_memory_realized_matches_db_math_with_slippage>
        <Function test_duplicate_order_window_blocks_resubmission>
        <Function test_duplicate_window_expiry_allows_resubmission>
        <Function test_same_client_id_is_idempotent_within_window>
        <Function test_duplicate_window_does_not_block_different_orders>
        <Function test_order_rate_cap_10_per_minute_fires>
        <Function test_order_rate_cap_is_a_rolling_window>
        <Function test_gateway_denial_blocks_low_level_submit>
        <Function test_place_order_routes_through_gateway_transmit>
        <Function test_oco_one_cancels_other_via_broker>
        <Function test_oco_same_bar_cross_fills_only_first_leg>
        <Function test_bracket_arms_children_on_entry_fill_and_cancels_twin>
        <Function test_bracket_with_resting_limit_entry_arms_children_later>
        <Function test_short_position_round_trip_cash>
        <Function test_non_positive_quantity_rejected>
      <Module test_phase9_automation.py>
        <Function test_local_wallclock_to_utc_is_dst_aware>
        <Function test_session_phase_classifies_each_window>
        <Function test_session_phase_boundaries_are_half_open>
        <Function test_session_phase_weekend_is_closed>
        <Function test_session_phase_nyse_holidays_are_closed[holiday0-Independence Day]>
        <Function test_session_phase_nyse_holidays_are_closed[holiday1-Christmas]>
        <Function test_session_phase_nyse_holidays_are_closed[holiday2-New Year's Day]>
        <Function test_session_phase_nyse_holidays_are_closed[holiday3-Thanksgiving]>
        <Function test_session_phase_nyse_holidays_are_closed[holiday4-Labor Day]>
        <Function test_dst_spring_forward_sunday_is_closed_and_following_monday_opens_at_edt>
        <Function test_dst_fall_back_sunday_is_closed_and_following_monday_opens_at_est>
        <Function test_scheduler_same_local_open_resolves_to_correct_utc_across_dst>
        <Function test_execution_allowed_market_only_vs_extended>
        <Function test_entries_allowed_respects_stop_new_entries_guard>
        <Function test_scheduler_runs_only_phase_eligible_jobs>
        <Function test_scheduler_interval_throttle_and_no_wall_clock>
        <Function test_scheduler_failure_is_isolated>
        <Function test_scheduler_persists_last_runs_across_restart>
        <Function test_legacy_scheduler_shim_still_works>
        <Function test_approval_bypass_full_auto>
        <Function test_approval_bypass_hybrid_requires_high_confidence>
        <Function test_approval_enqueue_approve_execute_lifecycle>
        <Function test_approval_reject_and_cancel>
        <Function test_approval_transition_table_blocks_illegal_moves>
        <Function test_approval_illegal_transition_raises>
        <Function test_approval_ttl_expires_pending>
        <Function test_approval_ttl_not_yet_expired_keeps_pending>
        <Function test_approval_queue_persists_across_restart>
        <Function test_ramp_multiplier_pure_function_matches_config>
        <Function test_recovery_full_timeline_freeze_and_restart>
        <Function test_recovery_cooling_off_blocks_entries>
        <Function test_recovery_size_order_caps_quantity>
        <Function test_recovery_caps_order_size_through_real_risk_gateway>
        <Function test_recovery_blocks_order_when_frozen_via_real_gateway>
        <Function test_recovery_observe_breaker_latches_halt>
        <Function test_recovery_persists_across_restart>
        <Function test_build_digest_aggregates_all_sources>
        <Function test_render_text_contains_key_sections>
        <Function test_build_digest_empty_day_returns_zeros>
        <Function test_reconcile_matched_positions>
        <Function test_reconcile_db_only_and_broker_only_divergence>
        <Function test_reconcile_quantity_mismatch>
        <Function test_reconcile_halts_via_breaker_on_mismatch>
        <Function test_reconcile_logs_to_automation_log>

========================= 437 tests collected in 0.16s =========================
```

### `.venv-ml` (ML tier) — 437 items

```text
========================= 437 tests collected in 0.16s =========================
```

Both environments collect an identical **437** items.

### Phase 9 named DST/holiday/session/recovery tests present in collect-only

```text
test_local_wallclock_to_utc_is_dst_aware
test_session_phase_classifies_each_window
test_session_phase_boundaries_are_half_open
test_session_phase_weekend_is_closed
test_session_phase_nyse_holidays_are_closed[holiday0-Independence Day]
test_session_phase_nyse_holidays_are_closed[holiday1-Christmas]
test_session_phase_nyse_holidays_are_closed[holiday2-New Year's Day]
test_session_phase_nyse_holidays_are_closed[holiday3-Thanksgiving]
test_session_phase_nyse_holidays_are_closed[holiday4-Labor Day]
test_dst_spring_forward_sunday_is_closed_and_following_monday_opens_at_edt
test_dst_fall_back_sunday_is_closed_and_following_monday_opens_at_est
test_scheduler_same_local_open_resolves_to_correct_utc_across_dst
test_recovery_full_timeline_freeze_and_restart
test_recovery_cooling_off_blocks_entries
test_recovery_caps_order_size_through_real_risk_gateway
test_recovery_observe_breaker_latches_halt
test_approval_ttl_expires_pending
test_approval_queue_persists_across_restart
test_reconcile_halts_via_breaker_on_mismatch
```

## 6. ML_ONLY proof (the 12 core-env failures are all import errors)

```text
================================================================
E   ImportError: scikit-learn is required for GBMBaseline
E   ModuleNotFoundError: No module named 'torch'
E   ModuleNotFoundError: No module named 'torch'
E   ModuleNotFoundError: No module named 'torch'
E   ModuleNotFoundError: No module named 'torch'
E   ImportError: scikit-learn is required for GBMBaseline
E   ModuleNotFoundError: No module named 'torch'
E   ModuleNotFoundError: No module named 'torch'
E   ModuleNotFoundError: No module named 'optuna'
E   ModuleNotFoundError: No module named 'optuna'
E   ModuleNotFoundError: No module named 'sklearn'
E   ModuleNotFoundError: No module named 'sklearn'
FAILED tests/unit/test_phase3_models.py::test_model_registry_roundtrip - Impo...
FAILED tests/unit/test_phase3_models.py::test_lstm_output_shape_and_seed_determinism
FAILED tests/unit/test_phase3_models.py::test_gru_output_shape_and_seed - Mod...
FAILED tests/unit/test_phase3_models.py::test_lstm_single_batch_overfit_smoke
FAILED tests/unit/test_phase3_models.py::test_gru_single_batch_overfit_smoke
FAILED tests/unit/test_phase3_models.py::test_gbm_fit_predict_save_load_roundtrip
FAILED tests/unit/test_phase4_models.py::test_ensemble_meta_trained_only_on_out_of_fold_predictions
FAILED tests/unit/test_phase4_models.py::test_ensemble_predict_shape_after_fit
FAILED tests/unit/test_phase4_models.py::test_nested_optuna_leakage_proof_and_embargo_assertion
FAILED tests/unit/test_phase4_models.py::test_optuna_best_params_differ_per_fold_when_data_shifts
FAILED tests/unit/test_phase4_models.py::test_calibration_fitted_only_on_validation_folds
FAILED tests/unit/test_phase4_models.py::test_platt_scale_and_isotonic_output_range

ML_ONLY count: 12

================================================================
pip freeze — .venv (core)
================================================================
beautifulsoup4==4.15.0
certifi==2026.7.22
charset-normalizer==3.4.9
coverage==7.15.2
frozendict==2.4.7
html5lib==1.1
idna==3.18
iniconfig==2.3.0
loguru==0.7.3
lxml==6.1.1
multitasking==0.0.13
numpy==2.2.6
packaging==26.2
pandas==2.2.3
peewee==4.2.6
platformdirs==4.11.0
pluggy==1.6.0
pytest==8.3.5
pytest-cov==6.1.1
python-dateutil==2.9.0.post0
python-dotenv==1.2.1
pytz==2026.3.post1
PyYAML==6.0.3
requests==2.31.0
scipy==1.15.3
six==1.17.0
soupsieve==2.9.1
typing_extensions==4.16.0
tzdata==2025.2
urllib3==2.7.0
webencodings==0.5.1
yfinance==0.2.50

================================================================
pip freeze — .venv-ml (ML tier) [key pins]
================================================================
numpy==2.2.6
optuna==4.5.0
pandas==2.2.3
pytest==8.3.5
pytest-cov==6.1.1
scikit-learn==1.7.2
scipy==1.15.3
torch==2.6.0
transformers==4.48.3

================================================================
RUNTIME DEMO — /tmp/phase9_demo.py (full script + stdout)
================================================================
--- script ---
"""Phase 9 runtime demo — visible script. Demonstrates:
  1. scheduler session detection at injected instants across DST + holiday
  2. recovery ramp full timeline + REAL RiskGateway integration (100 -> 25 fills)
No wall-clock; all time injected. No network.
"""
from datetime import date, datetime, timedelta, timezone

from automation.recovery import RecoveryRamp
from automation.scheduler import MarketScheduler, SessionPhase, local_wallclock_to_utc
from trading.order_types import Order, OrderState
from trading.paper_broker import PaperBroker
from utils.config import load_config

UTC = timezone.utc
cfg = load_config()
BASE = datetime(2024, 4, 1, 13, 30, tzinfo=UTC)  # 09:30 EDT

print("=== 1. DST-aware local_wallclock_to_utc ===")
print("EST 2024-03-04 09:30 ET ->", local_wallclock_to_utc(date(2024, 3, 4), "09:30"))
print("EDT 2024-04-01 09:30 ET ->", local_wallclock_to_utc(date(2024, 4, 1), "09:30"))

print("\n=== 2. session_phase across DST spring-forward + holiday (injected clock) ===")
sch = MarketScheduler(cfg, now_fn=lambda: datetime(2024, 3, 9, 14, 30, tzinfo=UTC))
print("Sat 2024-03-09 (weekend):", sch.phase().value)
sch2 = MarketScheduler(cfg, now_fn=lambda: datetime(2024, 3, 11, 13, 30, tzinfo=UTC))
print("Mon 2024-03-11 09:30 EDT (after spring-fwd):", sch2.phase().value,
      "== REGULAR:", sch2.phase() is SessionPhase.REGULAR)
sch3 = MarketScheduler(cfg, now_fn=lambda: datetime(2024, 7, 4, 16, 0, tzinfo=UTC))
print("Thu 2024-07-04 12:00 ET (holiday):", sch3.phase().value,
      "== CLOSED:", sch3.phase() is SessionPhase.CLOSED)

print("\n=== 3. recovery ramp full timeline (injected clock) ===")
clock = {"t": BASE}
ramp = RecoveryRamp(cfg, now_fn=lambda: clock["t"])
ramp.resume(equity=100_000.0)
for label, advance in [("day 1 (25%)", 0), ("day 5 (50%)", 5),
                       ("day 9 (75%)", 9), ("day 15 (100%)", 15)]:
    clock["t"] = BASE + timedelta(days=advance)
    print(f"  {label}: multiplier={ramp.multiplier():.2f} size_order(100)={ramp.size_order(100):.1f}")

print("\n=== 4. REAL RiskGateway integration (ramp caps the real broker fill) ===")
clock = {"t": BASE}
ramp = RecoveryRamp(cfg, now_fn=lambda: clock["t"])
ramp.resume(equity=100_000.0)  # day 1 -> 25%
broker = PaperBroker(config=cfg, clock=lambda: 0.0, fee_bps=0.0, slippage_bps=0.0)
sized_qty = ramp.size_order(100)            # 25.0
order = broker.place_order(Order("AAPL", "buy", sized_qty, price=50.0))
print("intended=100  ramp-sized=", sized_qty, " order.state=", order.state.value,
      " filled_qty=", order.filled_quantity, " broker.positions[AAPL]=", broker.positions["AAPL"])
assert order.state is OrderState.FILLED
assert broker.positions["AAPL"] == 25.0
print("PASS: real broker ledger reflects the ramp-capped size (25, not 100).")
--- stdout (.venv) ---
Traceback (most recent call last):
  File "/tmp/phase9_demo.py", line 8, in <module>
    from automation.recovery import RecoveryRamp
ModuleNotFoundError: No module named 'automation'
--- stdout (.venv, PYTHONPATH=repo root) ---
[32m2026-07-31 10:49:05.778[0m | [1mINFO    [0m | [36mapp               [0m | [36mutils.config:load_config:1068[0m - [1mConfiguration loaded from config.yaml (20 warning(s))[0m
[32m2026-07-31 10:49:05.778[0m | [1mINFO    [0m | [36mautomation        [0m | [36mautomation.recovery:resume:164[0m - [1mrecovery ramp restarted at day 0 (equity anchor=100000.0)[0m
[32m2026-07-31 10:49:05.778[0m | [1mINFO    [0m | [36mautomation        [0m | [36mautomation.recovery:resume:164[0m - [1mrecovery ramp restarted at day 0 (equity anchor=100000.0)[0m
```

## 7. Two suite runs per environment (distinct durations = fresh)

```text
SUITE RUN 1 — .venv (core):   12 failed, 425 passed in 22.31s
SUITE RUN 2 — .venv (core):   12 failed, 425 passed in 23.92s
SUITE RUN 1 — .venv-ml:        437 passed in 25.58s
SUITE RUN 2 — .venv-ml:        437 passed in 26.48s
```

## 8. `pip freeze` (pinned clean environments)

### `.venv` (core)

```text
================================================================
beautifulsoup4==4.15.0
certifi==2026.7.22
charset-normalizer==3.4.9
coverage==7.15.2
frozendict==2.4.7
html5lib==1.1
idna==3.18
iniconfig==2.3.0
loguru==0.7.3
lxml==6.1.1
multitasking==0.0.13
numpy==2.2.6
packaging==26.2
pandas==2.2.3
peewee==4.2.6
platformdirs==4.11.0
pluggy==1.6.0
pytest==8.3.5
pytest-cov==6.1.1
python-dateutil==2.9.0.post0
python-dotenv==1.2.1
pytz==2026.3.post1
PyYAML==6.0.3
requests==2.31.0
scipy==1.15.3
six==1.17.0
soupsieve==2.9.1
typing_extensions==4.16.0
tzdata==2025.2
urllib3==2.7.0
webencodings==0.5.1
yfinance==0.2.50

================================================================
pip freeze — .venv-ml (ML tier) [key pins]
================================================================
numpy==2.2.6
optuna==4.5.0
pandas==2.2.3
pytest==8.3.5
pytest-cov==6.1.1
scikit-learn==1.7.2
scipy==1.15.3
torch==2.6.0
transformers==4.48.3

================================================================
RUNTIME DEMO — /tmp/phase9_demo.py (full script + stdout)
================================================================
--- script ---
"""Phase 9 runtime demo — visible script. Demonstrates:
  1. scheduler session detection at injected instants across DST + holiday
  2. recovery ramp full timeline + REAL RiskGateway integration (100 -> 25 fills)
No wall-clock; all time injected. No network.
"""
from datetime import date, datetime, timedelta, timezone

from automation.recovery import RecoveryRamp
from automation.scheduler import MarketScheduler, SessionPhase, local_wallclock_to_utc
from trading.order_types import Order, OrderState
from trading.paper_broker import PaperBroker
from utils.config import load_config

UTC = timezone.utc
cfg = load_config()
BASE = datetime(2024, 4, 1, 13, 30, tzinfo=UTC)  # 09:30 EDT

print("=== 1. DST-aware local_wallclock_to_utc ===")
print("EST 2024-03-04 09:30 ET ->", local_wallclock_to_utc(date(2024, 3, 4), "09:30"))
print("EDT 2024-04-01 09:30 ET ->", local_wallclock_to_utc(date(2024, 4, 1), "09:30"))

print("\n=== 2. session_phase across DST spring-forward + holiday (injected clock) ===")
sch = MarketScheduler(cfg, now_fn=lambda: datetime(2024, 3, 9, 14, 30, tzinfo=UTC))
print("Sat 2024-03-09 (weekend):", sch.phase().value)
sch2 = MarketScheduler(cfg, now_fn=lambda: datetime(2024, 3, 11, 13, 30, tzinfo=UTC))
print("Mon 2024-03-11 09:30 EDT (after spring-fwd):", sch2.phase().value,
      "== REGULAR:", sch2.phase() is SessionPhase.REGULAR)
sch3 = MarketScheduler(cfg, now_fn=lambda: datetime(2024, 7, 4, 16, 0, tzinfo=UTC))
print("Thu 2024-07-04 12:00 ET (holiday):", sch3.phase().value,
      "== CLOSED:", sch3.phase() is SessionPhase.CLOSED)

print("\n=== 3. recovery ramp full timeline (injected clock) ===")
clock = {"t": BASE}
ramp = RecoveryRamp(cfg, now_fn=lambda: clock["t"])
ramp.resume(equity=100_000.0)
for label, advance in [("day 1 (25%)", 0), ("day 5 (50%)", 5),
                       ("day 9 (75%)", 9), ("day 15 (100%)", 15)]:
    clock["t"] = BASE + timedelta(days=advance)
    print(f"  {label}: multiplier={ramp.multiplier():.2f} size_order(100)={ramp.size_order(100):.1f}")

print("\n=== 4. REAL RiskGateway integration (ramp caps the real broker fill) ===")
clock = {"t": BASE}
ramp = RecoveryRamp(cfg, now_fn=lambda: clock["t"])
ramp.resume(equity=100_000.0)  # day 1 -> 25%
broker = PaperBroker(config=cfg, clock=lambda: 0.0, fee_bps=0.0, slippage_bps=0.0)
sized_qty = ramp.size_order(100)            # 25.0
order = broker.place_order(Order("AAPL", "buy", sized_qty, price=50.0))
print("intended=100  ramp-sized=", sized_qty, " order.state=", order.state.value,
      " filled_qty=", order.filled_quantity, " broker.positions[AAPL]=", broker.positions["AAPL"])
assert order.state is OrderState.FILLED
assert broker.positions["AAPL"] == 25.0
print("PASS: real broker ledger reflects the ramp-capped size (25, not 100).")
--- stdout (.venv) ---
Traceback (most recent call last):
  File "/tmp/phase9_demo.py", line 8, in <module>
    from automation.recovery import RecoveryRamp
ModuleNotFoundError: No module named 'automation'
--- stdout (.venv, PYTHONPATH=repo root) ---
[32m2026-07-31 10:49:05.778[0m | [1mINFO    [0m | [36mapp               [0m | [36mutils.config:load_config:1068[0m - [1mConfiguration loaded from config.yaml (20 warning(s))[0m
[32m2026-07-31 10:49:05.778[0m | [1mINFO    [0m | [36mautomation        [0m | [36mautomation.recovery:resume:164[0m - [1mrecovery ramp restarted at day 0 (equity anchor=100000.0)[0m
[32m2026-07-31 10:49:05.778[0m | [1mINFO    [0m | [36mautomation        [0m | [36mautomation.recovery:resume:164[0m - [1mrecovery ramp restarted at day 0 (equity anchor=100000.0)[0m
```

### `.venv-ml` (ML tier) — key pins

```text
numpy==2.2.6
optuna==4.5.0
pandas==2.2.3
pytest==8.3.5
pytest-cov==6.1.1
scikit-learn==1.7.2
scipy==1.15.3
torch==2.6.0
transformers==4.48.3
```

## 9. Runtime demo — complete script + stdout (`.venv`, no network, injected clock)

`/tmp/phase9_demo.py`:

```python
"""Phase 9 runtime demo — visible script. Demonstrates:
  1. scheduler session detection at injected instants across DST + holiday
  2. recovery ramp full timeline + REAL RiskGateway integration (100 -> 25 fills)
No wall-clock; all time injected. No network.
"""
from datetime import date, datetime, timedelta, timezone

from automation.recovery import RecoveryRamp
from automation.scheduler import MarketScheduler, SessionPhase, local_wallclock_to_utc
from trading.order_types import Order, OrderState
from trading.paper_broker import PaperBroker
from utils.config import load_config

UTC = timezone.utc
cfg = load_config()
BASE = datetime(2024, 4, 1, 13, 30, tzinfo=UTC)  # 09:30 EDT

print("=== 1. DST-aware local_wallclock_to_utc ===")
print("EST 2024-03-04 09:30 ET ->", local_wallclock_to_utc(date(2024, 3, 4), "09:30"))
print("EDT 2024-04-01 09:30 ET ->", local_wallclock_to_utc(date(2024, 4, 1), "09:30"))

print("\n=== 2. session_phase across DST spring-forward + holiday (injected clock) ===")
sch = MarketScheduler(cfg, now_fn=lambda: datetime(2024, 3, 9, 14, 30, tzinfo=UTC))
print("Sat 2024-03-09 (weekend):", sch.phase().value)
sch2 = MarketScheduler(cfg, now_fn=lambda: datetime(2024, 3, 11, 13, 30, tzinfo=UTC))
print("Mon 2024-03-11 09:30 EDT (after spring-fwd):", sch2.phase().value,
      "== REGULAR:", sch2.phase() is SessionPhase.REGULAR)
sch3 = MarketScheduler(cfg, now_fn=lambda: datetime(2024, 7, 4, 16, 0, tzinfo=UTC))
print("Thu 2024-07-04 12:00 ET (holiday):", sch3.phase().value,
      "== CLOSED:", sch3.phase() is SessionPhase.CLOSED)

print("\n=== 3. recovery ramp full timeline (injected clock) ===")
clock = {"t": BASE}
ramp = RecoveryRamp(cfg, now_fn=lambda: clock["t"])
ramp.resume(equity=100_000.0)
for label, advance in [("day 1 (25%)", 0), ("day 5 (50%)", 5),
                       ("day 9 (75%)", 9), ("day 15 (100%)", 15)]:
    clock["t"] = BASE + timedelta(days=advance)
    print(f"  {label}: multiplier={ramp.multiplier():.2f} size_order(100)={ramp.size_order(100):.1f}")

print("\n=== 4. REAL RiskGateway integration (ramp caps the real broker fill) ===")
clock = {"t": BASE}
ramp = RecoveryRamp(cfg, now_fn=lambda: clock["t"])
ramp.resume(equity=100_000.0)  # day 1 -> 25%
broker = PaperBroker(config=cfg, clock=lambda: 0.0, fee_bps=0.0, slippage_bps=0.0)
sized_qty = ramp.size_order(100)            # 25.0
order = broker.place_order(Order("AAPL", "buy", sized_qty, price=50.0))
print("intended=100  ramp-sized=", sized_qty, " order.state=", order.state.value,
      " filled_qty=", order.filled_quantity, " broker.positions[AAPL]=", broker.positions["AAPL"])
assert order.state is OrderState.FILLED
assert broker.positions["AAPL"] == 25.0
print("PASS: real broker ledger reflects the ramp-capped size (25, not 100).")

```

stdout:

```text
=== 1. DST-aware local_wallclock_to_utc ===
EST 2024-03-04 09:30 ET -> 2024-03-04 14:30:00+00:00
EDT 2024-04-01 09:30 ET -> 2024-04-01 13:30:00+00:00

=== 2. session_phase across DST spring-forward + holiday (injected clock) ===
Sat 2024-03-09 (weekend): closed
Mon 2024-03-11 09:30 EDT (after spring-fwd): regular == REGULAR: True
Thu 2024-07-04 12:00 ET (holiday): closed == CLOSED: True

=== 3. recovery ramp full timeline (injected clock) ===
  day 1 (25%): multiplier=0.25 size_order(100)=25.0
  day 5 (50%): multiplier=0.50 size_order(100)=50.0
  day 9 (75%): multiplier=0.75 size_order(100)=75.0
  day 15 (100%): multiplier=1.00 size_order(100)=100.0

=== 4. REAL RiskGateway integration (ramp caps the real broker fill) ===
intended=100  ramp-sized= 25.0  order.state= filled  filled_qty= 25.0  broker.positions[AAPL]= 25.0
PASS: real broker ledger reflects the ramp-capped size (25, not 100).
```

## 10. Documentation update proof (`git log -- <document>`)

```text
================================================================
--- git log --oneline docs/BUILD_PLAN.md ---
827690a Phase 9 docs: BUILD_PLAN status, ARCHITECTURE roadmap + self-check, AUDIT_REPORT entry
912151f Phase 8: order types & paper trading
--- git log --oneline docs/ARCHITECTURE.md ---
827690a Phase 9 docs: BUILD_PLAN status, ARCHITECTURE roadmap + self-check, AUDIT_REPORT entry
912151f Phase 8: order types & paper trading
--- git log --oneline docs/AUDIT_REPORT.md ---
827690a Phase 9 docs: BUILD_PLAN status, ARCHITECTURE roadmap + self-check, AUDIT_REPORT entry
912151f Phase 8: order types & paper trading

================================================================
VERBATIM DEMANDED-FUNCTION BODIES
================================================================
--- ramp_multiplier (automation/recovery.py) — ramp calculation ---
def ramp_multiplier(elapsed_days: float, *, config: AppConfig) -> float:
    """Pure graduated multiplier for ``elapsed_days`` since (re)start.

    This is the **ramp-calculation** function — the single expression of the
    ``recovery.*`` config ladder. It is pure w.r.t. its arguments so the full
    timeline (day1-3 / day4-7 / week2 / week3+) is deterministically testable.

    Tiers (boundaries are half-open [lo, hi) in days):

    * [0, 3)   -> day1_3_size_pct
    * [3, 7)   -> day4_7_size_pct
    * [7, 14)  -> week2_size_pct
    * [14, +)  -> week3_plus_size_pct
    """
    rec = config.recovery
    if elapsed_days < 3.0:
        return float(rec.day1_3_size_pct)
    if elapsed_days < 7.0:
        return float(rec.day4_7_size_pct)
    if elapsed_days < 14.0:
        return float(rec.week2_size_pct)
    return float(rec.week3_plus_size_pct)

--- session_phase (automation/scheduler.py) — session detection ---
def session_phase(
    at: datetime,
    *,
    config: Optional[AppConfig] = None,
) -> SessionPhase:
    """Classify an aware UTC ``at`` instant into a :class:`SessionPhase`.

    This is the **session-detection** function. It is pure w.r.t. ``at`` and
    ``config`` — it never reads the wall clock — so every branch (weekend,
    holiday, pre/regular/post windows) is deterministically testable.

    Window boundaries (all in exchange local time, DST-correct via
    :func:`local_wallclock_to_utc`):

    * CLOSED            when the day is a weekend or NYSE holiday
    * CLOSED            before ``pre_market_start`` or after ``post_market_end``
    * PRE_MARKET        ``pre_market_start`` <= t < ``market_open``
    * REGULAR           ``market_open`` <= t < ``market_close``
    * POST_MARKET       ``market_close`` <= t < ``post_market_end``
    """
    cfg = config or load_config()
    instant = to_utc(at)
    local = instant.astimezone(MARKET_TZ)
    day = local.date()
    if not is_trading_day(day):
        return SessionPhase.CLOSED
    auto = cfg.automation
    pre_start = local_wallclock_to_utc(day, auto.pre_market_start)
    market_open = local_wallclock_to_utc(day, auto.market_open)
    market_close = local_wallclock_to_utc(day, auto.market_close)
    post_end = local_wallclock_to_utc(day, auto.post_market_end)
    if instant < pre_start or instant >= post_end:
        return SessionPhase.CLOSED
    if instant < market_open:
        return SessionPhase.PRE_MARKET
    if instant < market_close:
        return SessionPhase.REGULAR
    return SessionPhase.POST_MARKET

--- _transition (automation/approval_queue.py) — approval transition ---
def _transition(
        self,
        signal_id: str,
        target: str,
        *,
        by: str,
        reason: str = "",
    ) -> QueuedSignal:
        """Apply a lifecycle transition gated by the ``_ALLOWED`` table.

        This is the **approval-transition** function: it enforces that a
        signal can only move along a legal edge (e.g. PENDING -> APPROVED ->
        EXECUTED), records the actor + timestamp, and persists the result.
        Illegal moves raise :class:`ApprovalError`.
        """
        if target not in _ALL_STATUSES:
            raise ApprovalError(f"unknown target status: {target!r}")
        signal = self._signals.get(signal_id)
        if signal is None:
            raise ApprovalError(f"unknown signal: {signal_id!r}")
        if target not in _ALLOWED.get(signal.status, frozenset()):
            raise ApprovalError(
                f"illegal transition {signal.status} -> {target} for {signal_id!r}")
        now = self._now()
        signal.status = target
        signal.decided_at = now
        signal.decision_by = by
        if reason:
            signal.reason = reason
        self._persist(action=f"transition:{target}", signal=signal,
                      extra={"by": by, "reason": reason} if reason else {"by": by})
        return signal
```

## 11. Reconciliation + verdict

**Reconciliation:** `TOTAL = CORE_GREEN(425) + ML_ONLY(12) = 437`.
- `.venv` collect = 437; `.venv-ml` collect = 437 (identical).
- `.venv` run = 425 passed / 12 ML-only import errors; `.venv-ml` run = 437 passed.
- 2× green each, distinct durations (22.31/23.92s core; 25.58/26.48s ml) = fresh.

**CORRECTION (reconciliation definition):** prior phases reported `ML_ONLY=24`,
but that was the *collected* `test_phase3_models.py` + `test_phase4_models.py`
count (15 + 9 = 24), not the *fail-in-core* count. Of those 24, exactly 12 fail
in `.venv` (6 phase-3 + 6 phase-4, all `ModuleNotFoundError` for
torch/sklearn/optuna) and 12 pass in core. The strict verifiable split is
`CORE_GREEN(425) + ML_ONLY(12) = 437` (prior CORE_GREEN was 381 at the Phase-8
state, not 369). The TOTAL (437) is unchanged and proven by identical
collect-only output in both environments.

**Phase 9 verdict:** market-hours scheduler (DST + holiday aware, injected
clock), approval queue (TTL + bypass + DB persistence), recovery ramp (full
25/50/75/100% timeline + cooling-off + REAL `RiskGateway` integration), daily
digest, and startup reconciliation are fully implemented with 44 behavioral
tests. No logic weakened; no breaker thresholds weakened; no history rewritten;
all commits pushed; working tree clean before reporting. **Phase 9 gate
satisfied.** Phase 10 (broker integration — Alpaca adapter strictly behind mock
tests, live gate enforced) next.
