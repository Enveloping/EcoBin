-- V62: keep the platform factory-progress poll bounded to one asset and
-- reliable-task type before checking the current factory-bag snapshot.

ALTER TABLE ops_reliable_task
    ADD INDEX ix_ops_task_factory_progress (
        source_device_asset_id,
        task_type,
        id DESC
    );
