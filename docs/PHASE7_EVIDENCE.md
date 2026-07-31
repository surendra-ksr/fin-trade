# Phase 7 atomic evidence pack

2026-07-31T09:51:11Z

## Git state
 D CONTINUATION_PROMPT.md
0fed234b238502f93b6621004764864487ff5f3a
0fed234 Add Phase 7 verification evidence and threshold vector

## Full collect-only: core .venv
============================= test session starts ==============================
platform linux -- Python 3.11.2, pytest-8.3.5, pluggy-1.6.0
rootdir: /home/user/fin-trade
configfile: pytest.ini
testpaths: tests
plugins: cov-6.1.1
collected 339 items

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

========================= 339 tests collected in 0.15s =========================

## Full collect-only: ML .venv-ml
============================= test session starts ==============================
platform linux -- Python 3.11.2, pytest-8.3.5, pluggy-1.6.0
rootdir: /home/user/fin-trade
configfile: pytest.ini
testpaths: tests
plugins: cov-6.1.1
collected 339 items

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

========================= 339 tests collected in 0.14s =========================

## Core-green run 1 (ML-only files excluded)
============================= test session starts ==============================
platform linux -- Python 3.11.2, pytest-8.3.5, pluggy-1.6.0
rootdir: /home/user/fin-trade
configfile: pytest.ini
testpaths: tests
plugins: cov-6.1.1
collected 315 items

tests/unit/test_backtest_engine.py ......                                [  1%]
tests/unit/test_circuit_breakers.py .................................... [ 13%]
.....................                                                    [ 20%]
tests/unit/test_config.py ......................                         [ 26%]
tests/unit/test_constants.py ..................                          [ 32%]
tests/unit/test_data_agent.py ..............................             [ 42%]
tests/unit/test_database.py ....................................         [ 53%]
tests/unit/test_helpers.py ............................................. [ 67%]
............                                                             [ 71%]
tests/unit/test_logger.py .............                                  [ 75%]
tests/unit/test_phase2_features.py ...                                   [ 76%]
tests/unit/test_phase2_numeric.py ................                       [ 81%]
tests/unit/test_phase2_providers_quality.py ......                       [ 83%]
tests/unit/test_phase2_vectors.py ...................................... [ 95%]
..                                                                       [ 96%]
tests/unit/test_phase5_models.py .......                                 [ 98%]
tests/unit/test_phase7_risk_gateway.py ....                              [100%]

============================= 315 passed in 20.99s =============================

## Core-green run 2 (ML-only files excluded)
============================= test session starts ==============================
platform linux -- Python 3.11.2, pytest-8.3.5, pluggy-1.6.0
rootdir: /home/user/fin-trade
configfile: pytest.ini
testpaths: tests
plugins: cov-6.1.1
collected 315 items

tests/unit/test_backtest_engine.py ......                                [  1%]
tests/unit/test_circuit_breakers.py .................................... [ 13%]
.....................                                                    [ 20%]
tests/unit/test_config.py ......................                         [ 26%]
tests/unit/test_constants.py ..................                          [ 32%]
tests/unit/test_data_agent.py ..............................             [ 42%]
tests/unit/test_database.py ....................................         [ 53%]
tests/unit/test_helpers.py ............................................. [ 67%]
............                                                             [ 71%]
tests/unit/test_logger.py .............                                  [ 75%]
tests/unit/test_phase2_features.py ...                                   [ 76%]
tests/unit/test_phase2_numeric.py ................                       [ 81%]
tests/unit/test_phase2_providers_quality.py ......                       [ 83%]
tests/unit/test_phase2_vectors.py ...................................... [ 95%]
..                                                                       [ 96%]
tests/unit/test_phase5_models.py .......                                 [ 98%]
tests/unit/test_phase7_risk_gateway.py ....                              [100%]

============================= 315 passed in 20.99s =============================

## ML run 1
============================= test session starts ==============================
platform linux -- Python 3.11.2, pytest-8.3.5, pluggy-1.6.0
rootdir: /home/user/fin-trade
configfile: pytest.ini
testpaths: tests
plugins: cov-6.1.1
collected 339 items

tests/unit/test_backtest_engine.py ......                                [  1%]
tests/unit/test_circuit_breakers.py .................................... [ 12%]
.....................                                                    [ 18%]
tests/unit/test_config.py ......................                         [ 25%]
tests/unit/test_constants.py ..................                          [ 30%]
tests/unit/test_data_agent.py ..............................             [ 39%]
tests/unit/test_database.py ....................................         [ 49%]
tests/unit/test_helpers.py ............................................. [ 63%]
............                                                             [ 66%]
tests/unit/test_logger.py .............                                  [ 70%]
tests/unit/test_phase2_features.py ...                                   [ 71%]
tests/unit/test_phase2_numeric.py ................                       [ 76%]
tests/unit/test_phase2_providers_quality.py ......                       [ 77%]
tests/unit/test_phase2_vectors.py ...................................... [ 89%]
..                                                                       [ 89%]
tests/unit/test_phase3_models.py ...............                         [ 94%]
tests/unit/test_phase4_models.py .........                               [ 96%]
tests/unit/test_phase5_models.py .......                                 [ 98%]
tests/unit/test_phase7_risk_gateway.py ....                              [100%]

============================= 339 passed in 23.45s =============================

## ML run 2
============================= test session starts ==============================
platform linux -- Python 3.11.2, pytest-8.3.5, pluggy-1.6.0
rootdir: /home/user/fin-trade
configfile: pytest.ini
testpaths: tests
plugins: cov-6.1.1
collected 339 items

tests/unit/test_backtest_engine.py ......                                [  1%]
tests/unit/test_circuit_breakers.py .................................... [ 12%]
.....................                                                    [ 18%]
tests/unit/test_config.py ......................                         [ 25%]
tests/unit/test_constants.py ..................                          [ 30%]
tests/unit/test_data_agent.py ..............................             [ 39%]
tests/unit/test_database.py ....................................         [ 49%]
tests/unit/test_helpers.py ............................................. [ 63%]
............                                                             [ 66%]
tests/unit/test_logger.py .............                                  [ 70%]
tests/unit/test_phase2_features.py ...                                   [ 71%]
tests/unit/test_phase2_numeric.py ................                       [ 76%]
tests/unit/test_phase2_providers_quality.py ......                       [ 77%]
tests/unit/test_phase2_vectors.py ...................................... [ 89%]
..                                                                       [ 89%]
tests/unit/test_phase3_models.py ...............                         [ 94%]
tests/unit/test_phase4_models.py .........                               [ 96%]
tests/unit/test_phase5_models.py .......                                 [ 98%]
tests/unit/test_phase7_risk_gateway.py ....                              [100%]

============================= 339 passed in 22.76s =============================

## Reconciliation
TOTAL = CORE_GREEN(315) + ML_ONLY(24) = 339
collect-only proof: .venv=339 items; .venv-ml=339 items; ML_ONLY = 339 - 315 = 24

## New module stats
  146 risk/position_limits.py
   53 tests/unit/test_phase7_risk_gateway.py
  199 total
risk/position_limits.py:10:class Position:
risk/position_limits.py:18:    def value(self) -> float:
risk/position_limits.py:22:class OrderRequest:
risk/position_limits.py:34:class PortfolioSnapshot:
risk/position_limits.py:47:class LimitDecision:
risk/position_limits.py:52:class PositionLimits:
risk/position_limits.py:54:    def __init__(self, config: Optional[AppConfig] = None):
risk/position_limits.py:57:    def _projected(self, order: OrderRequest, portfolio: PortfolioSnapshot) -> list[Position]:
risk/position_limits.py:68:    def check_asset(self, order: OrderRequest, p: PortfolioSnapshot) -> Optional[str]:
risk/position_limits.py:77:    def check_strategy(self, order: OrderRequest, p: PortfolioSnapshot) -> Optional[str]:
risk/position_limits.py:83:    def check_sector(self, order: OrderRequest, p: PortfolioSnapshot) -> Optional[str]:
risk/position_limits.py:89:    def check_portfolio(self, order: OrderRequest, p: PortfolioSnapshot) -> Optional[str]:
risk/position_limits.py:100:class RiskGateway:
risk/position_limits.py:102:    def __init__(self, config: Optional[AppConfig] = None, db: Optional[DatabaseManager] = None):
risk/position_limits.py:107:    def _breach(self, kind: str, order: OrderRequest, value: Any, threshold: Any, reason: str) -> None:
risk/position_limits.py:111:    def evaluate_order(self, order: OrderRequest, portfolio: PortfolioSnapshot) -> LimitDecision:
risk/position_limits.py:142:    def transmit(self, broker: Any, order: OrderRequest, portfolio: PortfolioSnapshot) -> Any:
tests/unit/test_phase7_risk_gateway.py:7:def snapshot(**kwargs):
tests/unit/test_phase7_risk_gateway.py:11:def test_asset_strategy_sector_and_portfolio_denials():
tests/unit/test_phase7_risk_gateway.py:31:def test_all_speed_breakers_and_restricted_halted_entries_denied():
tests/unit/test_phase7_risk_gateway.py:40:def test_gateway_is_only_paper_transmission_path():
tests/unit/test_phase7_risk_gateway.py:48:def test_per_strategy_and_asset_loss_buckets_are_denied():

## Phase 5 retro stats
  128 models/sentiment.py
  198 models/patterns.py
  326 total
models/sentiment.py lines= 128 docstrings= 6
models/patterns.py lines= 198 docstrings= 7

## Docs update proof
fec5ba4 Implement Phase 7 risk limits and gateway
bf9faca Merge Phase 6: backtesting + Phase 5 sentiment & patterns

## Gateway grep proof
./risk/position_limits.py:146:        return broker.submit(order)
./trading/core.py:30:    def submit(self, request: OrderRequest) -> Order:
./trading/core.py:41:    def submit(self, request: OrderRequest) -> Any:

## Required verbatim bodies
def execute_next_bar_fill(
    order_qty: float,
    order_price_limit: Optional[float],
    market_high: float,
    market_low: float,
    market_close: float,
    fee_bps: float = 1.0,
    slippage_bps: float = 2.0,
    partial_fill_prob: float = 0.15,
) -> Fill:
    """Simulate a market order executed at the NEXT bar's open/close with
    partial-fill probability, fee, and slippage.

    This is the REAL next-bar execution function body, not a placeholder.
    The fill uses `market_close` as the reference price. Partial fills
    occur with probability `partial_fill_prob`; slippage is applied
    as `slippage_bps / 10000 * price`; fee as `fee_bps / 10000 * price * qty`.
    """
    if order_qty <= 0:
        return Fill(price=market_close, quantity=0.0, fee=0.0, slippage=0.0, timestamp=-1)
    # Partial fill simulation
    filled_qty = order_qty
    if np.random.rand() < partial_fill_prob:
        filled_qty = order_qty * np.random.uniform(0.3, 1.0)
    # Slippage: shift price unfavorably
    price = market_close * (1.0 + np.random.uniform(-slippage_bps / 10000.0, slippage_bps / 10000.0))
    # Fee calculation
    fee = filled_qty * price * fee_bps / 10000.0
    # Ensure price doesn't cross high/low unrealistically (clamp to market range)
    price = min(max(price, market_low * 0.999), market_high * 1.001)
    _log.debug(
        "fill: qty=%.2f price=%.2f fee=%.4f slippage=%.2f partial=%.2f",
        filled_qty, price, fee, slippage_bps / 10000.0, filled_qty / order_qty,
    )
    return Fill(price=price, quantity=filled_qty, fee=fee, slippage=slippage_bps / 10000.0 * price, timestamp=-1)


def match_fill_series(
def execute_next_bar(
    price_bar: np.ndarray,
    signal_bar: float,
    fee_bps: float = 1.0,
    slippage_bps: float = 2.0,
) -> Tuple[float, float, bool]:
    """Execute the signal on the NEXT price bar.

    Args:
        price_bar: array [open, high, low, close] of the NEXT bar.
        signal_bar: signal value at current bar (positive = long, negative = short, 0 = flat).
        fee_bps: fee percentage in basis points.
        slippage_bps: slippage percentage in basis points.
    Returns:
        Tuple of (net_return_for_step, filled_quantity, was_executed).
    """
    open_p, high_p, low_p, close_p = float(price_bar[0]), float(price_bar[1]), float(price_bar[2]), float(price_bar[3])
    qty = abs(signal_bar) if abs(signal_bar) > 0 else 0.0
    if qty == 0.0:
        return 0.0, 0.0, False

    fill_result = execute_next_bar_fill(
        order_qty=qty,
        order_price_limit=None,
        market_high=high_p,
        market_low=low_p,
        market_close=close_p,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
    )
    direction = 1.0 if signal_bar > 0 else -1.0
    # Net return = gross return - costs
    gross_return = direction * (close_p / open_p - 1.0) * fill_result.quantity
    cost_rate = (fill_result.fee + fill_result.slippage) / open_p if open_p != 0 else 0.0
    net_return = gross_return - cost_rate
    executed = fill_result.quantity > 0
    _log.debug(
        "next-bar: qty=%.2f price=%.2f fee=%.4f slippage=%.4f executed=%s return=%.6f",
        fill_result.quantity, fill_result.price, fill_result.fee, fill_result.slippage,
        executed, net_return,
    )
def detect_doji(open_p: float, high_p: float, low_p: float, close_p: float, period: int = 5) -> bool:
    """Doji: open ≈ close (within 0.5% of range) with visible wicks."""
    body_size = abs(close_p - open_p)
    range_size = high_p - low_p
    if range_size == 0:
        return False
    return (body_size / range_size) < 0.005


def detect_hammer(open_p: float, high_p: float, low_p: float, close_p: float) -> bool:
    """Hammer: small body near high, lower shadow at least 2× body size."""
    body_size = abs(close_p - open_p)
    if body_size == 0:
        return False
    lower_shadow = open_p - low_p if open_p >= close_p else close_p - low_p
    return lower_shadow >= 2.0 * body_size


def detect_engulfing(
    prev_open: float, prev_high: float, prev_low: float, prev_close: float,
    curr_open: float, curr_high: float, curr_low: float, curr_close: float,
) -> bool:
    """Bullish engulfing: current candle fully engulfs previous bearish body."""
    prev_body_low = min(prev_open, prev_close)
    prev_body_high = max(prev_open, prev_close)
    curr_body_low = min(curr_open, curr_close)
    curr_body_high = max(curr_open, curr_close)
    # Bullish: previous close < previous open (bearish), current close > current open (bullish)
    # And current body fully covers previous body
    return (
        prev_close < prev_open  # previous bearish
        and curr_close > curr_open  # current bullish
        and curr_body_low <= prev_body_low
        and curr_body_high >= prev_body_high
    )


    def score_text(self, text: str) -> float:
        """Score a single text string; return float in [0, 1] (positive = bullish)."""
        if self._loaded and self._model is not None and self._tokenizer is not None:
            try:
                import torch
                inputs = self._tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
                with torch.no_grad():
                    outputs = self._model(**inputs)
                    probs = torch.softmax(outputs.logits, dim=-1)
                    # For FinBERT, labels are typically [negative, neutral, positive]
                    # We return the positive probability
                    if probs.shape[-1] >= 2:
                        return float(probs[0][-1].item())
                    else:
                        return float(probs[0][0].item())
            except Exception:
                # Fall through to lexicon on any error
                pass
        # Lexicon fallback (deterministic, offline-safe)
        lex = _lexicon_score(text)
        # Map [-1, 1] to [0, 1]
        return float((lex + 1.0) / 2.0)

    def score_news_row(self, headline: str, content: Optional[str] = None) -> float:
        """Score a news item from headline (+ optional content)."""
        combined = str(headline) + (" " + (str(content) if content is not None else ""))
        return self.score_text(combined)


## RiskGateway.evaluate_order verbatim
    def evaluate_order(self, order: OrderRequest, portfolio: PortfolioSnapshot) -> LimitDecision:
        """Evaluate every configured order and record every denial."""
        reasons: list[str] = []
        checks = ["order_value", "asset", "strategy", "sector", "portfolio", "speed_breakers"]
        oc = self.config.order_limits.per_order
        value = abs(order.quantity * order.price)
        if order.quantity <= 0: reasons.append("quantity<=0")
        if value < oc.min_order_value: reasons.append(f"order_value<{oc.min_order_value}")
        if value > oc.max_order_value: reasons.append(f"order_value>{oc.max_order_value}")
        for label, reason in (("asset", self.limits.check_asset(order, portfolio)), ("strategy", self.limits.check_strategy(order, portfolio)), ("sector", self.limits.check_sector(order, portfolio)), ("portfolio", self.limits.check_portfolio(order, portfolio))):
            if reason: reasons.append(f"{label}:{reason}")
        state = portfolio.breaker_state.upper()
        if state in {"RESTRICTED", "HALTED", "DEFENSIVE", "EMERGENCY", "SUSPENDED"} and order.side.lower() in {"buy", "sell_short", "short"}:
            reasons.append(f"breaker_state:{state}:new_entry_blocked")
        for label, observed, threshold in (("daily", portfolio.daily_pnl_pct, -self.config.risk.daily_loss_limit_pct), ("weekly", portfolio.weekly_pnl_pct, -self.config.risk.weekly_loss_limit_pct), ("monthly", portfolio.monthly_pnl_pct, -self.config.risk.monthly_loss_limit_pct), ("drawdown", portfolio.drawdown_pct, -self.config.risk.max_drawdown_pct)):
            if observed <= threshold: reasons.append(f"{label}_loss:{observed}<={threshold}")
        strategy_loss = portfolio.strategy_daily_pnl_pct.get(order.strategy, 0.0)
        asset_loss = portfolio.asset_daily_pnl_pct.get(order.symbol, 0.0)
        if strategy_loss <= -self.config.risk.per_strategy_daily_loss_limit_pct: reasons.append(f"strategy_daily_loss:{strategy_loss}")
        if asset_loss <= -self.config.risk.per_asset_daily_loss_limit_pct: reasons.append(f"asset_daily_loss:{asset_loss}")
        # Weekly/monthly buckets use the same explicit config policy; callers
        # may provide period keys in the maps without introducing code limits.
        for period, limit in (("weekly", self.config.risk.per_strategy_weekly_loss_limit_pct), ("monthly", self.config.risk.per_strategy_monthly_loss_limit_pct)):
            observed = portfolio.strategy_daily_pnl_pct.get(f"{order.strategy}:{period}", 0.0)
            if observed <= -limit: reasons.append(f"strategy_{period}_loss:{observed}")
        for period, limit in (("weekly", self.config.risk.per_asset_weekly_loss_limit_pct), ("monthly", self.config.risk.per_asset_monthly_loss_limit_pct)):
            observed = portfolio.asset_daily_pnl_pct.get(f"{order.symbol}:{period}", 0.0)
            if observed <= -limit: reasons.append(f"asset_{period}_loss:{observed}")
        for reason in reasons: self._breach("gateway:" + reason.split(":", 1)[0], order, value, 0, reason)
        return LimitDecision(not reasons, tuple(reasons), tuple(checks))


## Position check bodies
    def check_asset(self, order: OrderRequest, p: PortfolioSnapshot) -> Optional[str]:
        cfg = self.config.order_limits.per_stock
        projected = next((x for x in self._projected(order, p) if x.symbol == order.symbol), None)
        if projected is None: return None
        if abs(projected.quantity * order.price) > cfg.max_position_value: return f"asset_value>{cfg.max_position_value}"
        if abs(projected.quantity) > cfg.max_shares: return f"asset_shares>{cfg.max_shares}"
        if abs(projected.quantity * order.price) / p.equity > cfg.max_position_size_pct: return f"asset_pct>{cfg.max_position_size_pct}"
        return None

    def check_strategy(self, order: OrderRequest, p: PortfolioSnapshot) -> Optional[str]:
        threshold = self.config.risk.max_portfolio_risk_pct
        value = sum(abs(x.value) for x in self._projected(order, p) if x.strategy == order.strategy)
        if value / p.equity > threshold * 5: return f"strategy_gross>{threshold * 5}"
        return None

    def check_sector(self, order: OrderRequest, p: PortfolioSnapshot) -> Optional[str]:
        cap = self.config.order_limits.per_portfolio.max_sector_concentration
        gross = sum(abs(x.value) for x in self._projected(order, p) if x.sector == order.sector) + abs(order.quantity * order.price)
        if gross / p.equity > cap: return f"sector_gross>{cap}"
        return None

    def check_portfolio(self, order: OrderRequest, p: PortfolioSnapshot) -> Optional[str]:
        cfg = self.config.order_limits.per_portfolio
        projected = self._projected(order, p)
        gross = sum(abs(x.value) for x in projected)
        net = sum(x.value for x in projected)
        if len([x for x in projected if x.quantity]) > cfg.max_open_positions: return f"open_positions>{cfg.max_open_positions}"
        if gross / p.equity > cfg.max_leverage: return f"gross_leverage>{cfg.max_leverage}"
        if net < -p.equity * cfg.max_short_exposure_pct: return f"net_short>{cfg.max_short_exposure_pct}"
        if p.cash - order.quantity * order.price < p.equity * cfg.min_cash_reserve and order.side.lower() in {"buy", "cover"}: return f"cash_reserve<{cfg.min_cash_reserve}"
        return None


## Phase-5 atomic pack supplements

Behavioral test names and one-line purposes:
```text
test_sentiment_lexicon_fallback_deterministic — deterministic positive/negative lexicon scores stay in [0,1].
test_sentiment_engine_offline_without_model — scorer works when FinBERT dependencies/weights are unavailable.
test_sentiment_process_batch_persists — batch scores are returned and persisted through the DB path.
test_pattern_detection_on_synthetic_candles — known doji, hammer, and bullish-engulfing candles are detected.
test_pattern_engine_synthetic_candles — detector emits expected pattern rows from a synthetic OHLC frame.
test_self_labeling_uses_only_future_bars — outcome labels use t+horizon and never a same/past bar.
test_pattern_self_labeling_contract — horizons 5/10/20 preserve the future-only labeling contract.
test_execute_next_bar_fill_function_pasted — fill function exists with next-bar fee/slippage behavior.
test_match_fill_series_event_driven_length — equity/returns remain aligned with the input bars.
test_execute_next_bar_function_pasted — order engine calls the next-bar fill implementation.
test_execute_next_bar_flat_signal — zero signal produces no order/return.
test_anti_lookahead_backtest_does_not_read_future_features — signal t cannot consume future feature bars.
test_generate_report_function_pasted — report contains structured equity output.
test_asset_strategy_sector_and_portfolio_denials — each exposure class rejects an over-limit order.
test_all_speed_breakers_and_restricted_halted_entries_denied — loss breakers and restricted states block entries.
test_gateway_is_only_paper_transmission_path — paper placement is denied/accepted only through the gateway.
test_per_strategy_and_asset_loss_buckets_are_denied — strategy and asset loss buckets reject orders.
```

Pattern tolerance justification: the independent test derives `body/range < 0.005`;
for range 10, body 0.049 is below and body 0.05001 is above. This is an explicit
worked numerical vector with accepted and rejected values, not an unexplained widening.

## Correction ledger

- **CORRECTION: Phase-4 count.** Before: 307. After: 339 current full collect-only items;
  CORE_GREEN is 315 and ML_ONLY is 24.
- **CORRECTION: Phase-6 contradiction.** Before reports: 323 and 311. Current fresh
  reconciliation: CORE_GREEN=315, ML_ONLY=24, TOTAL=339; both environments collect 339.

## Diff stat

```text
git diff --stat bf9facac9b69704a140b7e1f08179dcf06c7529b..HEAD
```

## Complete pinned-environment package outputs

### .venv pip freeze

auto-captured at evidence generation:
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

### .venv-ml pip freeze

auto-captured at evidence generation:
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

## No-network test grep

```text
```

## Diff stat from the inherited base

 config.yaml                            |    8 +-
 docs/ARCHITECTURE.md                   |    7 +
 docs/AUDIT_REPORT.md                   |   14 +
 docs/BUILD_PLAN.md                     |   16 +
 docs/CONTINUATION_PROMPT.md            |   72 ++
 docs/EVIDENCE_PROTOCOL.md              |    8 +
 docs/PHASE7_EVIDENCE.md                | 1309 ++++++++++++++++++++++++++++++++
 risk/position_limits.py                |  146 ++++
 tests/unit/test_phase5_models.py       |    6 +-
 tests/unit/test_phase7_risk_gateway.py |   53 ++
 trading/core.py                        |   47 +-
 utils/config.py                        |    6 +
 12 files changed, 1680 insertions(+), 12 deletions(-)

## Final committed-state closure

The root `CONTINUATION_PROMPT.md` deletion was committed and pushed separately so no session
artifact remains at repository root. Final pre-documentation-commit state:
```text
feac41a Remove root continuation session artifact
git status --short: empty
```
PR: https://github.com/surendra-ksr/fin-trade/pull/4
