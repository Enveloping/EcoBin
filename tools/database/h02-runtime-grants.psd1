@{
    CatalogVersion = 8

    ReadOnlyTables = @(
        "iam_permission_definition"
    )

    SlotTables = @(
        "dev_asset_active_deployment"
        "dev_asset_active_tenant_allocation"
        "dev_device_occupancy"
        "rec_bag_current_occupancy"
        "fund_active_withdrawal"
    )

    # Exact P/O column grants from the reviewed F-04, F-05 and F-06 matrices.
    UpdateColumns = @{
        iam_platform_admin = @(
            "password_hash"
            "display_name"
            "enabled"
            "failed_login_count"
            "locked_until"
            "auth_version"
            "password_changed_at"
            "lock_version"
            "updated_at"
        )
        iam_tenant = @(
            "enterprise_name"
            "status"
            "contact_name"
            "contact_phone"
            "contact_address"
            "lock_version"
            "updated_at"
        )
        iam_organization = @(
            "organization_name"
            "status"
            "contact_phone"
            "contact_address"
            "lock_version"
            "updated_at"
        )
        iam_organization_miniapp = @(
            "appid"
            "display_name"
            "login_enabled"
            "secret_ref"
            "activated_at"
            "lock_version"
            "configured_at"
            "updated_at"
        )
        iam_staff_account = @(
            "password_hash"
            "display_name"
            "contact_phone"
            "enabled"
            "failed_login_count"
            "locked_until"
            "auth_version"
            "password_changed_at"
            "lock_version"
            "updated_at"
        )
        iam_organization_staff_membership = @(
            "is_manager"
            "enabled"
            "lock_version"
            "updated_at"
        )
        iam_staff_permission_grant = @(
            "revoked_at"
        )
        iam_staff_miniapp_binding = @(
            "status"
            "revoked_at"
            "revocation_reason"
            "lock_version"
        )
        iam_organization_user = @(
            "phone_e164"
            "phone_bound_at"
            "nickname"
            "avatar_url"
            "status"
            "auth_version"
            "lock_version"
            "frozen_at"
            "updated_at"
        )
        iam_organization_user_capability = @(
            "enabled"
            "granted_at"
            "revoked_at"
            "lock_version"
            "updated_at"
        )
        iam_platform_login_session = @(
            "revoked_at"
            "revocation_reason"
        )
        iam_staff_login_session = @(
            "revoked_at"
            "revocation_reason"
        )
        iam_organization_user_session = @(
            "revoked_at"
            "revocation_reason"
        )
        dev_device_asset = @(
            "lifecycle_status"
            "retired_at"
            "retirement_reason"
            "lock_version"
            "updated_at"
        )
        dev_device_deployment = @(
            "lifecycle_status"
            "business_enabled"
            "enabled_at"
            "ended_at"
            "end_method"
            "end_reason"
            "lock_version"
            "updated_at"
        )
        dev_asset_tenant_allocation = @(
            "status"
            "ended_by_platform_admin_id"
            "ended_at"
            "end_mode"
            "end_reason"
            "lock_version"
            "updated_at"
        )
        dev_config_application = @(
            "status"
            "reported_version_no"
            "reported_content_sha256"
            "reported_mcu_payload_sha256"
            "edge_persisted_at"
            "mcu_synced_at"
            "applied_at"
            "last_failure_at"
            "last_failure_code"
            "lock_version"
            "updated_at"
        )
        dev_device_transport_state = @(
            "onenet_connection_status"
            "status_observed_at"
            "status_received_at"
            "evidence_source"
            "source_inbox_id"
            "lock_version"
            "updated_at"
        )
        dev_deployment_runtime_state = @(
            "edge_connection_status"
            "mcu_link_status"
            "safety_status"
            "aggregate_weight_health"
            "camera_health"
            "local_storage_health"
            "clock_sync_health"
            "edge_boot_id"
            "edge_software_version"
            "mcu_firmware_version"
            "mcu_boot_id"
            "uart_state"
            "uart_protocol_major"
            "uart_protocol_minor"
            "capability_bitmap_hex"
            "last_mcu_reset_reason"
            "applied_config_version_no"
            "applied_config_content_sha256"
            "applied_mcu_payload_sha256"
            "local_storage_state"
            "clock_state"
            "pending_reliable_event_count"
            "trusted_runtime_edge_event_id"
            "trusted_runtime_edge_event_type"
            "trusted_runtime_sequence"
            "trusted_runtime_received_at"
            "orange_pi_reported_config_version_no"
            "orange_pi_reported_config_content_sha256"
            "orange_pi_reported_config_mcu_payload_sha256"
            "safety_projection_edge_event_id"
            "safety_projection_edge_event_type"
            "safety_projection_sequence"
            "last_heartbeat_at"
            "last_device_event_at"
            "lock_version"
            "updated_at"
        )
        dev_port_runtime_state = @(
            "delivery_door_state"
            "delivery_door_actuator_health"
            "delivery_door_contact_state"
            "clean_lock_power_state"
            "clean_solenoid_health"
            "clean_door_inferred_state"
            "clean_door_state_basis"
            "cleaner_physical_close_confirmed"
            "last_delivery_door_command"
            "last_delivery_door_output_status"
            "delivery_door_physical_state_basis"
            "weight_sensor_health"
            "weight_measurement_uid"
            "weight_measurement_status"
            "weight_value_available"
            "reported_weight_grams"
            "weight_value_kind"
            "weight_measurement_elapsed_ms"
            "weight_sample_count"
            "calibration_version"
            "infrared_value"
            "infrared_sensor_health"
            "fullness_sensor_kind"
            "fullness_sensor_value"
            "fullness_sample_basis"
            "representative_distance_mm"
            "fullness_valid_sample_count"
            "smoke_state"
            "smoke_sensor_health"
            "runtime_fault_bitmap"
            "trusted_runtime_edge_event_id"
            "trusted_runtime_edge_event_type"
            "trusted_runtime_sequence"
            "safety_projection_edge_event_id"
            "safety_projection_edge_event_type"
            "safety_projection_sequence"
            "safety_status"
            "pending_delivery_result_session_id"
            "last_observed_at"
            "lock_version"
            "updated_at"
        )
        dev_device_fault_event = @(
            "impact_level"
            "status"
            "last_detected_at"
            "discovery_count"
            "recovery_source_kind"
            "recovery_source_edge_event_id"
            "recovery_source_edge_event_type"
            "recovery_method"
            "recovery_audit_log_id"
            "recovered_at"
            "recovered_by_staff_account_id"
            "recovery_reason"
            "device_recovery_observed_edge_event_id"
            "device_recovery_observed_edge_event_type"
            "device_recovery_observed_at"
            "lock_version"
        )
        dev_delivery_session = @(
            "status"
            "first_edge_accepted_at"
            "first_physical_progress_at"
            "device_completed_at"
            "ended_at"
            "end_reason"
            "lock_version"
            "updated_at"
        )
        dev_device_command = @(
            "physical_state"
            "edge_accepted_at"
            "physical_started_at"
            "physical_ended_at"
            "lock_version"
            "updated_at"
        )
        rec_organization_delivery_config_head = @(
            "current_config_id"
            "current_version_no"
            "lock_version"
            "switched_at"
            "updated_at"
        )
        rec_organization_order_counter = @(
            "last_visibility_sequence_no"
            "lock_version"
            "updated_at"
        )
        rec_delivery_order = @(
            "review_status"
            "current_revision_no"
            "current_revision_id"
            "final_business_weight_kg"
            "final_amount_cent"
            "first_approved_at"
            "updated_at"
        )
        rec_delivery_photo = @(
            "photo_uid"
            "status"
            "object_url"
            "sha256"
            "size_bytes"
            "captured_at"
            "linked_at"
            "missing_reason"
            "updated_at"
        )
        rec_organization_clean_config_head = @(
            "current_config_id"
            "current_version_no"
            "lock_version"
            "switched_at"
            "updated_at"
        )
        rec_organization_clean_record_counter = @(
            "last_visibility_sequence_no"
            "lock_version"
            "updated_at"
        )
        rec_clean_operation = @(
            "pre_unlock_weight_status"
            "pre_unlock_weight_g"
            "pre_unlock_weight_fault_code"
            "status"
            "edge_saved_confirmed"
            "first_unlock_may_have_executed"
            "clean_lock_deenergized_confirmed"
            "cleaner_physical_close_confirmed"
            "edge_saved_at"
            "first_possible_unlock_at"
            "solenoid_powered_off_at"
            "cleaner_confirmed_closed_at"
            "pre_unlock_end_requested_at"
            "recovery_requested_at"
            "reopen_count"
            "recovery_count"
            "completion_record_id"
            "ended_at"
            "end_reason"
            "lock_version"
            "updated_at"
        )
        rec_clean_record = @(
            "recalculated_removed_net_weight_status"
            "recalculated_removed_net_weight_g"
            "effective_removed_net_weight_g"
            "effective_weight_source"
            "record_remark"
            "lock_version"
            "updated_at"
        )
        rec_clean_photo = @(
            "photo_uid"
            "status"
            "object_url"
            "sha256"
            "size_bytes"
            "captured_at"
            "linked_at"
            "missing_reason"
            "updated_at"
        )
        rec_photo_terminal_fact = @(
            "delivery_photo_id"
            "clean_photo_id"
            "linked_at"
            "updated_at"
        )
        dev_photo_upload_grant_request = @(
            "request_status"
            "updated_at"
        )
        rec_port_baseline_measurement = @(
            "status"
            "physical_result_id"
            "stable_total_weight_g"
            "fault_code"
            "result_baseline_id"
            "started_at"
            "completed_at"
            "lock_version"
            "updated_at"
        )
        rec_port_capacity_state = @(
            "baseline_state"
            "current_baseline_id"
            "current_baseline_weight_g"
            "latest_stable_total_weight_g"
            "raw_net_weight_g"
            "displayed_fullness_percent"
            "detection_gate"
            "current_detection_id"
            "current_rule_fingerprint"
            "confirmed_fullness_state"
            "last_detection_id"
            "current_fullness_event_id"
            "current_bag_id"
            "current_fullness_state_change_id"
            "last_fullness_edge_event_id"
            "last_fullness_edge_event_sequence"
            "last_fullness_reported_at"
            "lock_version"
            "updated_at"
        )
        rec_fullness_detection = @(
            "status"
            "final_result"
            "failure_code"
            "disposition"
            "initial_sample_id"
            "initial_sample_conclusion"
            "terminal_sample_id"
            "terminal_sample_conclusion"
            "next_sample_at"
            "completed_at"
            "lock_version"
            "updated_at"
        )
        rec_fullness_event = @(
            "status"
            "confirmed_detection_id"
            "confirmed_at"
            "current_reason"
            "latest_detection_id"
            "latest_full_detection_id"
            "detection_count"
            "recovered_by_detection_id"
            "recovered_at"
            "updated_at"
        )
        fund_wechat_merchant_profile = @(
            "status"
            "scene_id"
            "report_type"
            "report_content"
            "transfer_page_style"
            "non_secret_config_ref"
            "lock_version"
            "updated_at"
        )
        fund_miniapp_merchant_binding = @(
            "status"
            "disabled_at"
            "lock_version"
            "updated_at"
        )
        fund_payout_gate = @(
            "gate_state"
            "current_pause_event_id"
            "paused_at"
            "lock_version"
            "updated_at"
        )
        fund_organization_withdraw_config_head = @(
            "current_config_id"
            "current_version_no"
            "lock_version"
            "switched_at"
            "updated_at"
        )
        fund_organization_wallet_entry_counter = @(
            "last_visibility_sequence_no"
            "lock_version"
            "updated_at"
        )
        fund_user_wallet = @(
            "available_balance_cent"
            "frozen_withdrawal_cent"
            "last_entry_sequence_no"
            "delivery_gate_state"
            "delivery_gate_threshold_snapshot_cent"
            "delivery_gate_trigger_entry_id"
            "delivery_gate_latched_at"
            "lock_version"
            "updated_at"
        )
        fund_organization_payout_account = @(
            "available_payout_cent"
            "frozen_withdrawal_cent"
            "lock_version"
            "updated_at"
        )
        fund_recharge_order = @(
            "business_state"
            "paid_at"
            "posted_at"
            "closed_at"
            "lock_version"
            "updated_at"
        )
        fund_wechat_payment = @(
            "code_url"
            "transaction_id"
            "channel_state"
            "last_api_error_code"
            "channel_updated_at"
            "lock_version"
            "updated_at"
        )
        fund_withdrawal_order = @(
            "business_state"
            "negative_balance_pause"
            "post_boundary_risk"
            "pre_channel_block_reason"
            "channel_boundary_at"
            "long_unsettled_at"
            "reviewed_at"
            "channel_terminal_at"
            "ended_at"
            "lock_version"
            "updated_at"
        )
        fund_wechat_transfer = @(
            "transfer_bill_no"
            "channel_state"
            "terminal_classification"
            "package_info"
            "last_api_error_code"
            "terminal_fail_reason"
            "state_conflict"
            "channel_updated_at"
            "terminal_at"
            "lock_version"
            "updated_at"
        )
        ops_inbox_message = @(
            "processing_state"
            "last_received_at"
            "delivery_count"
            "processed_at"
            "lock_version"
            "updated_at"
        )
        ops_message_quarantine = @(
            "last_seen_at"
            "discovery_count"
            "status"
            "acknowledged_audit_id"
            "acknowledged_at"
            "lock_version"
            "updated_at"
        )
        ops_reliable_task = @(
            "state"
            "next_run_at"
            "lease_token"
            "lease_worker"
            "lease_until"
            "attempt_sequence"
            "consecutive_failure_count"
            "wake_version"
            "handled_wake_version"
            "completed_at"
            "blocked_reason_code"
            "blocked_diagnostic"
            "dispatch_wait_reason"
            "lock_version"
            "updated_at"
        )
        ops_task_attempt = @(
            "lease_until"
            "external_call_may_have_started_at"
            "reclaimed_at"
            "result_recorded_at"
            "action_kind"
            "technical_result"
            "request_sha256"
            "response_sha256"
            "http_status"
            "external_api_error_code"
            "duration_ms"
            "redacted_diagnostic"
        )
        ops_alert = @(
            "current_severity"
            "highest_severity"
            "last_seen_at"
            "discovery_count"
            "safe_display_parameters"
            "acknowledged_at"
            "acknowledged_audit_id"
            "status"
            "resolved_at"
            "lock_version"
            "updated_at"
        )
        ops_reconciliation_run = @(
            "state"
            "started_at"
            "last_progress_at"
            "completed_at"
            "checked_recharge_count"
            "checked_withdrawal_count"
            "deterministic_repair_count"
            "issue_count_at_completion"
            "lock_version"
            "updated_at"
        )
        ops_reconciliation_issue = @(
            "severity"
            "latest_seen_run_id"
            "last_seen_at"
            "discovery_count"
            "state"
            "last_handled_at"
            "last_handled_audit_id"
            "system_verified_resolved_at"
            "lock_version"
            "updated_at"
        )
    }

    PendingUpdateTables = @()
}
