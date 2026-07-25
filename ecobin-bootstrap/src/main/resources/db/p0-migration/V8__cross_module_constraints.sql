-- Close only relationships that could not be created before both endpoint
-- tables existed. Foreign-key checks remain enabled throughout.

ALTER TABLE rec_organization_delivery_config
    ADD CONSTRAINT uq_rec_delivery_config_session_identity UNIQUE (
        tenant_id,
        organization_id,
        id,
        content_sha256,
        open_balance_floor_cent,
        max_review_abs_weight_g
    );

ALTER TABLE rec_bag
    ADD CONSTRAINT uq_rec_bag_session_identity UNIQUE (
        tenant_id,
        organization_id,
        id,
        bag_code
    );

ALTER TABLE dev_delivery_session
    ADD CONSTRAINT fk_dev_delivery_session_delivery_config
        FOREIGN KEY (
            tenant_id,
            organization_id,
            delivery_config_version_id,
            delivery_config_content_sha256,
            open_balance_floor_cent,
            max_review_abs_weight_g
        )
        REFERENCES rec_organization_delivery_config (
            tenant_id,
            organization_id,
            id,
            content_sha256,
            open_balance_floor_cent,
            max_review_abs_weight_g
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_dev_delivery_session_bag
        FOREIGN KEY (
            tenant_id,
            organization_id,
            bag_id,
            bag_code_snapshot
        )
        REFERENCES rec_bag (
            tenant_id,
            organization_id,
            id,
            bag_code
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_dev_delivery_session_delivery_config_fk (
        tenant_id,
        organization_id,
        delivery_config_version_id,
        delivery_config_content_sha256,
        open_balance_floor_cent,
        max_review_abs_weight_g
    ),
    ADD INDEX ix_dev_delivery_session_bag_fk (
        tenant_id,
        organization_id,
        bag_id,
        bag_code_snapshot
    );

ALTER TABLE rec_clean_operation
    ADD CONSTRAINT uq_rec_clean_operation_command_ref
        UNIQUE (tenant_id, organization_id, deployment_id, id);

ALTER TABLE rec_fullness_detection
    ADD CONSTRAINT uq_rec_fullness_detection_command_ref
        UNIQUE (tenant_id, organization_id, deployment_id, id);

ALTER TABLE rec_port_baseline_measurement
    ADD CONSTRAINT uq_rec_baseline_measurement_command_ref
        UNIQUE (tenant_id, organization_id, deployment_id, id),
    ADD CONSTRAINT uq_rec_baseline_measurement_result_ref
        UNIQUE (
            tenant_id,
            organization_id,
            deployment_id,
            port_id,
            id
        );

ALTER TABLE dev_device_occupancy
    ADD CONSTRAINT fk_dev_occupancy_clean_operation
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            clean_operation_id
        )
        REFERENCES rec_clean_operation (
            tenant_id,
            organization_id,
            deployment_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_dev_occupancy_clean_operation_fk (
        tenant_id,
        organization_id,
        deployment_id,
        clean_operation_id
    );

ALTER TABLE dev_device_command
    ADD CONSTRAINT fk_dev_command_clean_operation
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            clean_operation_id
        )
        REFERENCES rec_clean_operation (
            tenant_id,
            organization_id,
            deployment_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_dev_command_fullness_detection
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            fullness_detection_id
        )
        REFERENCES rec_fullness_detection (
            tenant_id,
            organization_id,
            deployment_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_dev_command_baseline_measurement
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            baseline_measurement_id
        )
        REFERENCES rec_port_baseline_measurement (
            tenant_id,
            organization_id,
            deployment_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_dev_command_clean_operation_fk (
        tenant_id,
        organization_id,
        deployment_id,
        clean_operation_id
    ),
    ADD INDEX ix_dev_command_fullness_detection_fk (
        tenant_id,
        organization_id,
        deployment_id,
        fullness_detection_id
    ),
    ADD INDEX ix_dev_command_baseline_measurement_fk (
        tenant_id,
        organization_id,
        deployment_id,
        baseline_measurement_id
    );

ALTER TABLE dev_physical_result
    ADD CONSTRAINT fk_dev_result_clean_operation
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            port_id,
            clean_operation_id
        )
        REFERENCES rec_clean_operation (
            tenant_id,
            organization_id,
            deployment_id,
            port_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_dev_result_fullness_sample
        FOREIGN KEY (
            tenant_id,
            organization_id,
            fullness_sample_id
        )
        REFERENCES rec_fullness_sample (
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_dev_result_baseline_measurement
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            port_id,
            baseline_measurement_id
        )
        REFERENCES rec_port_baseline_measurement (
            tenant_id,
            organization_id,
            deployment_id,
            port_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_dev_result_clean_operation_fk (
        tenant_id,
        organization_id,
        deployment_id,
        port_id,
        clean_operation_id
    ),
    ADD INDEX ix_dev_result_fullness_sample_fk (
        tenant_id,
        organization_id,
        fullness_sample_id
    ),
    ADD INDEX ix_dev_result_baseline_measurement_fk (
        tenant_id,
        organization_id,
        deployment_id,
        port_id,
        baseline_measurement_id
    );

ALTER TABLE iam_organization_miniapp
    ADD CONSTRAINT uq_iam_miniapp_funds_ref
        UNIQUE (tenant_id, organization_id, id, appid);

ALTER TABLE iam_organization_user
    ADD CONSTRAINT uq_iam_org_user_funds_identity_ref UNIQUE (
        tenant_id,
        organization_id,
        id,
        organization_miniapp_id,
        openid
    );

ALTER TABLE fund_miniapp_merchant_binding
    ADD CONSTRAINT fk_fund_binding_miniapp_appid
        FOREIGN KEY (
            tenant_id,
            organization_id,
            organization_miniapp_id,
            appid
        )
        REFERENCES iam_organization_miniapp (
            tenant_id,
            organization_id,
            id,
            appid
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_fund_binding_miniapp_appid_fk (
        tenant_id,
        organization_id,
        organization_miniapp_id,
        appid
    );

ALTER TABLE fund_withdrawal_order
    ADD CONSTRAINT fk_fund_withdrawal_recipient_identity
        FOREIGN KEY (
            tenant_id,
            organization_id,
            organization_user_id,
            organization_miniapp_id,
            openid_snapshot
        )
        REFERENCES iam_organization_user (
            tenant_id,
            organization_id,
            id,
            organization_miniapp_id,
            openid
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_fund_withdrawal_recipient_identity_fk (
        tenant_id,
        organization_id,
        organization_user_id,
        organization_miniapp_id,
        openid_snapshot
    );

ALTER TABLE dev_edge_event
    ADD COLUMN source_scope_kind VARCHAR(16) CHARACTER SET ascii
        COLLATE ascii_bin
        GENERATED ALWAYS AS ('ORGANIZATION') STORED,
    ADD CONSTRAINT fk_dev_edge_event_inbox_identity
        FOREIGN KEY (source_inbox_id)
        REFERENCES ops_inbox_message (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_dev_edge_event_inbox_scope
        FOREIGN KEY (
            source_scope_kind,
            tenant_id,
            organization_id,
            source_inbox_id
        )
        REFERENCES ops_inbox_message (
            scope_kind,
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_dev_edge_event_inbox_scope_fk (
        source_scope_kind,
        tenant_id,
        organization_id,
        source_inbox_id
    );

ALTER TABLE dev_device_fault_event
    ADD COLUMN recovery_audit_scope_kind VARCHAR(16) CHARACTER SET ascii
        COLLATE ascii_bin
        GENERATED ALWAYS AS ('ORGANIZATION') STORED,
    ADD CONSTRAINT fk_dev_fault_recovery_audit_identity
        FOREIGN KEY (recovery_audit_log_id)
        REFERENCES ops_audit_log (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_dev_fault_recovery_audit_scope
        FOREIGN KEY (
            recovery_audit_scope_kind,
            tenant_id,
            organization_id,
            recovery_audit_log_id
        )
        REFERENCES ops_audit_log (
            scope_kind,
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_dev_fault_recovery_audit_identity_fk (
        recovery_audit_log_id
    ),
    ADD INDEX ix_dev_fault_recovery_audit_scope_fk (
        recovery_audit_scope_kind,
        tenant_id,
        organization_id,
        recovery_audit_log_id
    );

ALTER TABLE fund_user_wallet
    ADD CONSTRAINT fk_fund_user_wallet_gate_entry
        FOREIGN KEY (
            tenant_id,
            organization_id,
            delivery_gate_trigger_entry_id,
            id
        )
        REFERENCES fund_user_wallet_entry (
            tenant_id,
            organization_id,
            id,
            wallet_id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_fund_user_wallet_gate_entry_fk (
        tenant_id,
        organization_id,
        delivery_gate_trigger_entry_id,
        id
    );

ALTER TABLE fund_payout_gate
    ADD COLUMN current_pause_event_type VARCHAR(16) CHARACTER SET ascii
        COLLATE ascii_bin
        GENERATED ALWAYS AS (
            CASE
                WHEN gate_state = 'PAUSED_NOT_ENOUGH' THEN 'PAUSED'
                ELSE NULL
            END
        ) STORED,
    ADD CONSTRAINT fk_fund_payout_gate_current_pause
        FOREIGN KEY (
            merchant_profile_id,
            current_pause_event_id,
            current_pause_event_type
        )
        REFERENCES fund_payout_gate_event (
            merchant_profile_id,
            id,
            event_type
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_fund_payout_gate_current_pause_fk (
        merchant_profile_id,
        current_pause_event_id,
        current_pause_event_type
    );

ALTER TABLE fund_wallet_adjustment
    ADD COLUMN audit_scope_kind VARCHAR(16) CHARACTER SET ascii
        COLLATE ascii_bin
        GENERATED ALWAYS AS ('ORGANIZATION') STORED,
    ADD CONSTRAINT fk_fund_wallet_adjustment_audit_identity
        FOREIGN KEY (succeeded_audit_id)
        REFERENCES ops_audit_log (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_fund_wallet_adjustment_audit_scope
        FOREIGN KEY (
            audit_scope_kind,
            tenant_id,
            organization_id,
            succeeded_audit_id
        )
        REFERENCES ops_audit_log (
            scope_kind,
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_fund_wallet_adjustment_audit_scope_fk (
        audit_scope_kind,
        tenant_id,
        organization_id,
        succeeded_audit_id
    );

ALTER TABLE fund_withdrawal_review
    ADD COLUMN audit_scope_kind VARCHAR(16) CHARACTER SET ascii
        COLLATE ascii_bin
        GENERATED ALWAYS AS ('ORGANIZATION') STORED,
    ADD CONSTRAINT fk_fund_withdrawal_review_audit_identity
        FOREIGN KEY (succeeded_audit_id)
        REFERENCES ops_audit_log (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_fund_withdrawal_review_audit_scope
        FOREIGN KEY (
            audit_scope_kind,
            tenant_id,
            organization_id,
            succeeded_audit_id
        )
        REFERENCES ops_audit_log (
            scope_kind,
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_fund_withdrawal_review_audit_scope_fk (
        audit_scope_kind,
        tenant_id,
        organization_id,
        succeeded_audit_id
    );

ALTER TABLE fund_wechat_payment_observation
    ADD CONSTRAINT fk_fund_payment_observation_inbox_identity
        FOREIGN KEY (source_inbox_id)
        REFERENCES ops_inbox_message (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_fund_payment_observation_inbox_scope
        FOREIGN KEY (
            source_scope_kind,
            tenant_id,
            organization_id,
            source_inbox_id
        )
        REFERENCES ops_inbox_message (
            scope_kind,
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_fund_payment_observation_attempt_identity
        FOREIGN KEY (source_task_attempt_id)
        REFERENCES ops_task_attempt (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_fund_payment_observation_attempt_scope
        FOREIGN KEY (
            source_scope_kind,
            tenant_id,
            organization_id,
            source_task_attempt_id
        )
        REFERENCES ops_task_attempt (
            scope_kind,
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT;

ALTER TABLE fund_wechat_transfer_observation
    ADD CONSTRAINT fk_fund_transfer_observation_inbox_identity
        FOREIGN KEY (source_inbox_id)
        REFERENCES ops_inbox_message (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_fund_transfer_observation_inbox_scope
        FOREIGN KEY (
            source_scope_kind,
            tenant_id,
            organization_id,
            source_inbox_id
        )
        REFERENCES ops_inbox_message (
            scope_kind,
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_fund_transfer_observation_attempt_identity
        FOREIGN KEY (source_task_attempt_id)
        REFERENCES ops_task_attempt (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_fund_transfer_observation_attempt_scope
        FOREIGN KEY (
            source_scope_kind,
            tenant_id,
            organization_id,
            source_task_attempt_id
        )
        REFERENCES ops_task_attempt (
            scope_kind,
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT;
