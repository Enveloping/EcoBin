-- Factory acceptance runs before tenant and organization assignment. Its
-- reliable evidence therefore needs a platform-scoped CONFIRM_EDGE_EVENT task
-- anchored directly to the permanent device asset.

ALTER TABLE ops_reliable_task
    DROP CHECK ck_ops_task_sources_v36,
    ADD CONSTRAINT ck_ops_task_sources_v37 CHECK (
        (
            task_category = 'INBOX_PROCESSING'
            AND source_inbox_id IS NOT NULL
            AND source_device_asset_id IS NULL
            AND source_device_command_id IS NULL
        )
        OR
        (
            task_category <> 'INBOX_PROCESSING'
            AND source_inbox_id IS NULL
            AND (
                (
                    source_device_asset_id IS NULL
                    AND source_device_command_id IS NULL
                )
                OR
                (
                    source_device_asset_id IS NOT NULL
                    AND (
                        (
                            scope_kind = 'PLATFORM'
                            AND tenant_id IS NULL
                            AND organization_id IS NULL
                            AND source_device_command_id IS NULL
                            AND task_type IN (
                                'REQUEST_DEVICE_ACCEPTANCE',
                                'CONFIRM_EDGE_EVENT'
                            )
                        )
                        OR
                        (
                            scope_kind = 'ORGANIZATION'
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
            )
        )
    );
