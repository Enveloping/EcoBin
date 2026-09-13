-- Continuous OneNet-offline occupancy release.
-- Releasing the usage slot does not end the original business or change bag facts.
ALTER TABLE dev_device_transport_state
    ADD COLUMN offline_since_at DATETIME(3) NULL AFTER status_received_at,
    ADD INDEX ix_transport_offline_since (
        onenet_connection_status,
        offline_since_at,
        asset_id
    );

-- Historical OFFLINE rows do not contain a trustworthy first-offline time.
-- Start their conservative 600-second window when this migration is applied.
UPDATE dev_device_transport_state
SET offline_since_at = UTC_TIMESTAMP(3)
WHERE onenet_connection_status = 'OFFLINE';

ALTER TABLE dev_delivery_session
    ADD COLUMN offline_occupancy_released_at DATETIME(3) NULL
        AFTER end_reason,
    ADD INDEX ix_delivery_offline_release_pending (
        asset_id,
        offline_occupancy_released_at,
        ended_at,
        id
    );

ALTER TABLE rec_clean_operation
    ADD COLUMN offline_occupancy_released_at DATETIME(3) NULL
        AFTER end_reason,
    ADD INDEX ix_clean_offline_release_pending (
        asset_id,
        offline_occupancy_released_at,
        ended_at,
        id
    );
