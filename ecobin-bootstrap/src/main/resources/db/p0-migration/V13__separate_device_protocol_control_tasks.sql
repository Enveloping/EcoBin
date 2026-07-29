-- Protocol-control messages use the DEVICE execution lane but are not
-- physical device commands. Keep them anchored to a deployment without
-- creating synthetic rows in dev_device_command.

ALTER TABLE dev_device_command
    DROP FOREIGN KEY fk_dev_command_confirmation_edge,
    DROP INDEX uq_dev_command_confirmation_event,
    DROP CHECK ck_dev_command_target_shape,
    DROP COLUMN confirmation_source_edge_event_type,
    DROP COLUMN confirmation_source_edge_event_id,
    ADD CONSTRAINT ck_dev_command_target_shape CHECK (
        (
            command_type = 'START_DELIVERY_SESSION'
            AND delivery_session_id IS NOT NULL
            AND clean_operation_id IS NULL
            AND config_application_id IS NULL
            AND fullness_detection_id IS NULL
            AND baseline_measurement_id IS NULL
        )
        OR
        (
            command_type IN (
                'START_CLEAN_OPERATION',
                'END_CLEAN_BEFORE_UNLOCK',
                'RESUME_CLEAN_OPERATION'
            )
            AND delivery_session_id IS NULL
            AND clean_operation_id IS NOT NULL
            AND config_application_id IS NULL
            AND fullness_detection_id IS NULL
            AND baseline_measurement_id IS NULL
        )
        OR
        (
            command_type = 'APPLY_CONFIGURATION'
            AND delivery_session_id IS NULL
            AND clean_operation_id IS NULL
            AND config_application_id IS NOT NULL
            AND fullness_detection_id IS NULL
            AND baseline_measurement_id IS NULL
        )
        OR
        (
            command_type = 'SAMPLE_FULLNESS'
            AND delivery_session_id IS NULL
            AND clean_operation_id IS NULL
            AND config_application_id IS NULL
            AND fullness_detection_id IS NOT NULL
            AND baseline_measurement_id IS NULL
        )
        OR
        (
            command_type = 'MEASURE_EMPTY_BAG_BASELINE'
            AND delivery_session_id IS NULL
            AND clean_operation_id IS NULL
            AND config_application_id IS NULL
            AND fullness_detection_id IS NULL
            AND baseline_measurement_id IS NOT NULL
        )
    );

ALTER TABLE ops_reliable_task
    DROP CHECK ck_ops_task_sources,
    ADD CONSTRAINT ck_ops_task_sources CHECK (
        (
            task_category = 'INBOX_PROCESSING'
            AND source_inbox_id IS NOT NULL
            AND source_device_deployment_id IS NULL
            AND source_device_command_id IS NULL
        )
        OR
        (
            task_category <> 'INBOX_PROCESSING'
            AND source_inbox_id IS NULL
            AND (
                (
                    source_device_deployment_id IS NULL
                    AND source_device_command_id IS NULL
                )
                OR
                (
                    scope_kind = 'ORGANIZATION'
                    AND source_device_deployment_id IS NOT NULL
                    AND (
                        source_device_command_id IS NOT NULL
                        OR
                        (
                            source_device_command_id IS NULL
                            AND task_type IN (
                                'CONFIRM_EDGE_EVENT',
                                'PROVIDE_PHOTO_UPLOAD_GRANT'
                            )
                        )
                    )
                )
            )
        )
    ),
    ADD CONSTRAINT fk_ops_task_device_deployment
        FOREIGN KEY (
            tenant_id,
            organization_id,
            source_device_deployment_id
        )
        REFERENCES dev_device_deployment (
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT;
