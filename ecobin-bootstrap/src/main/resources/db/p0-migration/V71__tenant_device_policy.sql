-- One platform default and an optional complete tenant override.
RENAME TABLE dev_fullness_policy TO dev_device_default_policy;
ALTER TABLE dev_device_default_policy
    ADD COLUMN unit_price_yuan_per_kg DECIMAL(15,4) NOT NULL DEFAULT 0.4500,
    ADD COLUMN negative_weight_threshold_g BIGINT NOT NULL DEFAULT 500,
    ADD CONSTRAINT ck_dev_default_policy_price CHECK (unit_price_yuan_per_kg BETWEEN 0.0001 AND 429496.7295),
    ADD CONSTRAINT ck_dev_default_policy_negative CHECK (negative_weight_threshold_g BETWEEN 1 AND 4294967295);

-- Re-publish even when the old fullness values happen to match. Historical
-- configuration snapshots and their price remain immutable.
UPDATE dev_device_default_policy
SET policy_version = policy_version + 1,
    rollout_uid = '00000000-0000-4000-8000-000000000071', rollout_status = 'PENDING',
    next_asset_id = 0, target_asset_count = 0, processed_asset_count = 0, published_asset_count = 0,
    publication_source = 'SYSTEM', updated_by_platform_admin_id = NULL,
    change_reason = 'V71 租户统一设备配置：继承平台默认',
    started_at = UTC_TIMESTAMP(3), completed_at = NULL, updated_at = UTC_TIMESTAMP(3),
    lock_version = lock_version + 1
WHERE singleton_id = 1;

-- MySQL forbids renaming a column while a CHECK still references its old name.
ALTER TABLE dev_config_version DROP CHECK ck_dev_config_fullness_policy_version;
ALTER TABLE dev_config_version
    RENAME COLUMN fullness_policy_version_no TO device_default_policy_version_no,
    RENAME INDEX ix_dev_config_fullness_policy TO ix_dev_config_default_policy,
    ADD COLUMN tenant_device_policy_version_no BIGINT NULL AFTER device_default_policy_version_no,
    ADD CONSTRAINT ck_dev_config_default_policy_version CHECK (
        device_default_policy_version_no IS NULL OR device_default_policy_version_no BETWEEN 1 AND 9007199254740991
    ),
    ADD CONSTRAINT ck_dev_config_tenant_policy_version CHECK (
        (tenant_device_policy_version_no IS NULL OR tenant_device_policy_version_no BETWEEN 1 AND 9007199254740991)
        AND (device_default_policy_version_no IS NULL OR tenant_device_policy_version_no IS NULL)
    ),
    ADD INDEX ix_dev_config_tenant_policy (tenant_id, asset_id, tenant_device_policy_version_no, version_no DESC);

CREATE TABLE dev_tenant_device_policy (
    tenant_id BIGINT NOT NULL,
    policy_version BIGINT NOT NULL,
    configuration_mode VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    unit_price_yuan_per_kg DECIMAL(15,4) NULL,
    fullness_mode VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    fullness_weight_kg DECIMAL(10,3) NULL,
    negative_weight_threshold_g BIGINT NULL,
    rollout_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    rollout_status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    next_asset_id BIGINT NOT NULL DEFAULT 0,
    target_asset_count BIGINT NOT NULL DEFAULT 0,
    processed_asset_count BIGINT NOT NULL DEFAULT 0,
    published_asset_count BIGINT NOT NULL DEFAULT 0,
    updated_by_staff_account_id BIGINT NOT NULL,
    change_reason VARCHAR(500) NOT NULL,
    started_at DATETIME(3) NOT NULL,
    completed_at DATETIME(3) NULL,
    updated_at DATETIME(3) NOT NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id),
    INDEX ix_dev_tenant_policy_rollout (rollout_status, tenant_id),
    CONSTRAINT fk_dev_tenant_policy_tenant FOREIGN KEY (tenant_id) REFERENCES iam_tenant(id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_tenant_policy_staff FOREIGN KEY (tenant_id, updated_by_staff_account_id) REFERENCES iam_staff_account(tenant_id, id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_dev_tenant_policy_version CHECK (policy_version BETWEEN 1 AND 9007199254740991 AND lock_version >= 0),
    CONSTRAINT ck_dev_tenant_policy_values CHECK (
        (configuration_mode = 'INHERIT' AND unit_price_yuan_per_kg IS NULL AND fullness_mode IS NULL AND fullness_weight_kg IS NULL AND negative_weight_threshold_g IS NULL)
        OR (configuration_mode = 'CUSTOM' AND unit_price_yuan_per_kg IS NOT NULL AND fullness_mode IS NOT NULL AND fullness_weight_kg IS NOT NULL AND negative_weight_threshold_g IS NOT NULL
            AND unit_price_yuan_per_kg BETWEEN 0.0001 AND 429496.7295
            AND fullness_mode IN ('INFRARED_ONLY', 'WEIGHT_ONLY', 'INFRARED_OR_WEIGHT')
            AND fullness_weight_kg BETWEEN 0.001 AND 4294967.295 AND negative_weight_threshold_g BETWEEN 1 AND 4294967295)
    ),
    CONSTRAINT ck_dev_tenant_policy_uid CHECK (rollout_uid = LOWER(rollout_uid) AND rollout_uid REGEXP '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'),
    CONSTRAINT ck_dev_tenant_policy_rollout CHECK (
        rollout_status IN ('PENDING', 'RUNNING', 'DONE') AND next_asset_id >= 0 AND target_asset_count >= 0
        AND processed_asset_count >= 0 AND published_asset_count BETWEEN 0 AND processed_asset_count
        AND ((rollout_status IN ('PENDING', 'RUNNING') AND completed_at IS NULL) OR (rollout_status = 'DONE' AND completed_at IS NOT NULL))
    ),
    CONSTRAINT ck_dev_tenant_policy_reason CHECK (CHAR_LENGTH(TRIM(change_reason)) BETWEEN 1 AND 500),
    CONSTRAINT ck_dev_tenant_policy_times CHECK (updated_at >= started_at AND (completed_at IS NULL OR completed_at >= started_at))
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
