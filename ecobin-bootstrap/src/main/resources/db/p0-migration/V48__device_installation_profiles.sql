-- V48: move the current installation identity and location from immutable
-- machine configuration history to the permanent physical device asset.

ALTER TABLE dev_device_asset
    ADD COLUMN installation_display_name VARCHAR(100) NULL
        AFTER expected_port_count,
    ADD COLUMN installation_address VARCHAR(500) NULL
        AFTER installation_display_name,
    ADD COLUMN installation_latitude DECIMAL(10, 7) NULL
        AFTER installation_address,
    ADD COLUMN installation_longitude DECIMAL(10, 7) NULL
        AFTER installation_latitude,
    ADD COLUMN installation_profile_version BIGINT NOT NULL DEFAULT 0
        AFTER installation_longitude,
    ADD COLUMN installation_updated_by_organization_user_id BIGINT NULL
        AFTER installation_profile_version,
    ADD COLUMN installation_updated_at DATETIME(3) NULL
        AFTER installation_updated_by_organization_user_id;

-- The newest pre-V48 configuration is the one-time migration source. Devices
-- without a configuration receive a stable display name and an incomplete
-- profile; the cleaner completes it during installation.
UPDATE dev_device_asset asset
LEFT JOIN (
    SELECT ranked.asset_id,
           ranked.device_display_name,
           ranked.location_address,
           ranked.latitude,
           ranked.longitude,
           ranked.published_at
    FROM (
        SELECT config.asset_id,
               config.device_display_name,
               config.location_address,
               config.latitude,
               config.longitude,
               config.published_at,
               ROW_NUMBER() OVER (
                   PARTITION BY config.asset_id
                   ORDER BY config.version_no DESC, config.id DESC
               ) row_no
        FROM dev_config_version config
    ) ranked
    WHERE ranked.row_no = 1
) latest ON latest.asset_id = asset.id
SET asset.installation_display_name = COALESCE(
        NULLIF(TRIM(latest.device_display_name), ''),
        CONCAT(
            '回收箱 ',
            CONVERT(asset.hardware_sn USING utf8mb4)
                COLLATE utf8mb4_0900_ai_ci
        )
    ),
    asset.installation_address = latest.location_address,
    asset.installation_latitude = CASE
        WHEN latest.latitude IS NOT NULL
             AND latest.longitude IS NOT NULL
        THEN latest.latitude
        ELSE NULL
    END,
    asset.installation_longitude = CASE
        WHEN latest.latitude IS NOT NULL
             AND latest.longitude IS NOT NULL
        THEN latest.longitude
        ELSE NULL
    END,
    asset.installation_updated_at = GREATEST(
        COALESCE(latest.published_at, asset.created_at),
        asset.created_at
    );

ALTER TABLE dev_device_asset
    MODIFY COLUMN installation_display_name VARCHAR(100) NOT NULL,
    MODIFY COLUMN installation_updated_at DATETIME(3) NOT NULL,
    ADD CONSTRAINT ck_dev_asset_installation_display_name_v48 CHECK (
        CHAR_LENGTH(TRIM(installation_display_name)) > 0
    ),
    ADD CONSTRAINT ck_dev_asset_installation_coordinates_v48 CHECK (
        (
            installation_latitude IS NULL
            AND installation_longitude IS NULL
        )
        OR
        (
            installation_latitude IS NOT NULL
            AND installation_longitude IS NOT NULL
            AND installation_latitude BETWEEN -90.0000000 AND 90.0000000
            AND installation_longitude BETWEEN -180.0000000 AND 180.0000000
        )
    ),
    ADD CONSTRAINT ck_dev_asset_installation_profile_v48 CHECK (
        installation_profile_version >= 0
        AND (
            installation_profile_version = 0
            OR
            (
                installation_address IS NOT NULL
                AND CHAR_LENGTH(TRIM(installation_address)) > 0
                AND installation_latitude IS NOT NULL
                AND installation_longitude IS NOT NULL
                AND installation_updated_by_organization_user_id IS NOT NULL
            )
        )
    ),
    ADD CONSTRAINT ck_dev_asset_installation_time_v48 CHECK (
        installation_updated_at >= created_at
    ),
    ADD CONSTRAINT fk_dev_asset_installation_updater_v48
        FOREIGN KEY (
            tenant_id,
            organization_id,
            installation_updated_by_organization_user_id
        ) REFERENCES iam_organization_user (
            tenant_id,
            organization_id,
            id
        ) ON DELETE RESTRICT ON UPDATE RESTRICT;

-- These columns remain as nullable legacy history for schema-v1 snapshots.
-- Schema-v2 and later configuration rows no longer write installation data.
ALTER TABLE dev_config_version
    MODIFY COLUMN device_display_name VARCHAR(100) NULL,
    ADD CONSTRAINT ck_dev_config_no_installation_fields_v48 CHECK (
        schema_version < 2
        OR
        (
            device_display_name IS NULL
            AND location_address IS NULL
            AND latitude IS NULL
            AND longitude IS NULL
        )
    );
