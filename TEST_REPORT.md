# Z-SPECTRA test inventory

Pytest discovery found **101 test cases** in this checkout. The collection command lists tests without executing them. The previously reported full test run completed with **101 passed**. No tests were rerun to create this inventory.

Run the suite from the repository root with:

```sh
python3 -m pytest
```

## Collected test cases

### `test_audit`

- `test_valid_bank_to_mesh_payload_passes`
- `test_boundary_violation_is_a_compatibility_alias`
- `test_valid_mesh_to_llm_payload_passes`
- `test_valid_mesh_to_bank_payload_passes`
- `test_unknown_or_wrong_payload_is_blocked_and_counted[bank_to_mesh]`
- `test_unknown_or_wrong_payload_is_blocked_and_counted[mesh_to_llm]`
- `test_unknown_or_wrong_payload_is_blocked_and_counted[mesh_to_bank]`
- `test_extra_keys_are_rejected`
- `test_non_finite_feature_is_rejected`
- `test_long_strings_are_rejected`
- `test_invalid_bank_to_mesh_fields_are_blocked[seq-True]`
- `test_invalid_bank_to_mesh_fields_are_blocked[t_ms-1.5]`
- `test_invalid_bank_to_mesh_fields_are_blocked[src_bid-BID_AAAAAAAAAAAA]`
- `test_invalid_bank_to_mesh_fields_are_blocked[amt_bin-1.1]`
- `test_invalid_bank_to_mesh_fields_are_blocked[amt_bin-True]`
- `test_invalid_bank_to_mesh_fields_are_blocked[f-value5]`
- `test_invalid_bank_to_mesh_fields_are_blocked[f-value6]`
- `test_invalid_bank_to_mesh_fields_are_blocked[sensor_us--0.1]`
- `test_unknown_channel_is_blocked`
- `test_canary_scanner_does_not_retain_source_strings_and_counts_hits`
- `test_self_test_scan_counters_do_not_affect_production_counters`
- `test_auditor_reset_clears_blocked_counter`
### `test_audit_logging`

- `test_digest_index_scans_without_retaining_source_canaries`
- `test_negative_control_is_always_caught_and_reset_clears_counters`
- `test_logging_handler_scans_formatted_log_line`
### `test_bank_service`

- `test_bank_runtime_rejects_a_different_training_compression_profile`
- `test_event_path_sends_only_validated_blind_payload`
- `test_hold_only_changes_local_account_state`
- `test_reset_clears_bank_hold_and_audit_state`
### `test_bench`

- `test_distribution_reports_observed_percentiles_and_sample_count`
- `test_summary_aggregates_actual_seed_values`
- `test_cascade_outcomes_use_ground_truth_value_and_alert_timestamps`
- `test_live_load_test_replays_each_speed_and_summarizes_observed_alert_metrics`
- `test_readme_results_snapshot_comes_from_all_requested_result_sections`
- `test_readme_snapshot_does_not_claim_dfltcc_from_the_cpu_flag_alone`
### `test_canary_index`

- `test_index_contains_digests_and_scanner_finds_source_value`
### `test_chain`

- `test_links_cross_bank_hops_and_inherits_chain`
- `test_accepts_clock_skew_with_clipped_feature_gap`
- `test_old_event_does_not_link`
### `test_dashboard`

- `test_results_endpoint_reads_only_the_configured_results_file`
- `test_heist_button_forwards_only_a_control_request`
- `test_metrics_endpoint_proxies_mesh_decision_measurements`
### `test_generator`

- `GeneratorTests::test_abort_storms_are_observed_as_rollback_telemetry`
- `GeneratorTests::test_all_scenarios_exist_and_slow_drip_is_in_final_time_segment`
- `GeneratorTests::test_canaries_are_fake_sentinels_present_in_raw_data`
- `GeneratorTests::test_ground_truth_contains_deterministic_two_percent_false_positive_noise`
- `GeneratorTests::test_minimum_reduced_config_keeps_exact_event_count`
- `GeneratorTests::test_raw_events_follow_private_raw_shape_and_have_no_labels`
- `GeneratorTests::test_seed_makes_all_outputs_deterministic`
### `test_heist`

- `test_heist_has_hop_two_transfer_and_later_cashout`
- `test_run_heist_routes_each_event_to_its_bank`
### `test_http_session_pool`

- `test_pool_reuses_sessions_between_short_lived_threads_without_concurrent_use`
### `test_llm`

- `LLMBriefTests::test_api_errors_use_template_fallback`
- `LLMBriefTests::test_api_mode_without_a_url_uses_the_template`
- `LLMBriefTests::test_api_receives_only_blind_payload_json_and_never_returns_key`
- `LLMBriefTests::test_local_llm_request_does_not_require_an_api_key`
- `LLMBriefTests::test_rejects_extra_top_level_fields_after_audit`
- `LLMBriefTests::test_slow_local_server_uses_template_after_the_request_timeout`
- `LLMBriefTests::test_template_fallback_is_deterministic_and_audited_first`
### `test_mesh_features`

- `test_mesh_features_use_linked_chain_only`
- `test_first_hop_mesh_features_are_zero_where_no_prior_hop_exists`
### `test_mesh_service`

- `test_heist_alerts_at_hop_two_and_recommends_hold_for_received_account`
- `test_reset_clears_mesh_chain_and_alert_state`
- `test_negative_control_message_is_caught_by_the_shared_scanner`
- `test_hop_one_anomaly_is_promoted_when_the_chain_becomes_a_cascade`
- `test_alert_is_visible_before_slow_llm_brief_arrives`
### `test_models`

- `test_model_fit_uses_distinct_anomaly_and_classifier_splits`
- `test_thresholds_are_calibrated_and_evidence_uses_frozen_feature_names`
### `test_packaging`

- `test_only_bank_container_receives_the_consortium_key`
- `test_runtime_dependencies_are_limited_to_the_frozen_stack`
- `test_only_the_mesh_image_installs_numpy_and_scikit_learn`
- `test_docker_context_excludes_local_data_and_mesh_receives_only_the_digest_index`
- `test_results_json_is_trackable_for_submission`
- `test_linuxone_preflight_reports_binary_wheel_availability_separately`
- `test_each_docker_image_checks_its_runtime_imports_during_build`
- `test_dfltcc_is_opt_in_guarded_and_bank_runtime_checks_its_own_libz`
### `test_platform_proof`

- `test_dfltcc_requires_s390x_cpu_flag_and_loaded_zlib_support`
- `test_dfltcc_guard_rejects_missing_host_evidence`
### `test_prepare_demo`

- `test_prepared_artifacts_are_bound_to_the_configured_key_without_saving_it`
- `test_prepared_model_is_invalidated_when_compression_runtime_changes`
### `test_process_runner`

- `test_process_mode_keeps_the_consortium_key_only_in_bank_environments`
### `test_replay_service`

- `test_heist_control_requires_token_and_never_returns_raw_event`
- `test_benchmark_route_reports_measured_hop_two_delivery`
- `test_benchmark_route_rejects_unsupported_speed_factor`
- `test_benchmark_route_rejects_boolean_speed_factor`
### `test_schema`

- `test_frozen_sensor_feature_order`
- `test_mesh_feature_order`
- `test_wire_contract_key_sets_are_frozen`
### `test_sensor`

- `BlindSensorTests::test_bid_is_stable_and_canonicalizes_account_text`
- `BlindSensorTests::test_compression_features_reuse_the_window_and_cached_reference_sizes`
- `BlindSensorTests::test_features_use_the_frozen_order_and_quarter_decade_amount_bin`
- `BlindSensorTests::test_output_is_only_the_frozen_bank_to_mesh_contract`
- `BlindSensorTests::test_rolling_counters_are_account_scoped_and_time_bounded`
- `BlindSensorTests::test_rolling_history_counts_match_after_ordered_and_late_events`
- `BlindSensorTests::test_window_caps_at_last_32_events_and_reference_is_stable`
- `BlindSensorTests::test_window_is_source_scoped_and_has_only_coarse_blind_rows`
### `test_train`

- `test_time_splits_keep_ground_truth_chains_together`
- `test_slow_drip_remains_in_test_split`
### `test_walking_skeleton`

- `test_two_hop_walking_skeleton_emits_hold_to_local_bank`
### `test_zlib_bench`

- `test_configured_toggle_runs_in_fresh_processes_and_records_comparable_times`
- `test_unconfigured_toggle_reports_baseline_without_calling_it_hardware_off`
