-- V70: one fullness rule for all tenants, devices and ports.
-- Keep historical configurations; rollout creates newer complete versions.

CREATE TABLE dev_fullness_policy (
    singleton_id TINYINT NOT NULL,
    policy_version BIGINT NOT NULL,
    fullness_mode VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    fullness_weight_kg DECIMAL(10,3) NOT NULL,
    rollout_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    rollout_status VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    next_asset_id BIGINT NOT NULL DEFAULT 0,
    target_asset_count BIGINT NOT NULL DEFAULT 0,
    processed_asset_count BIGINT NOT NULL DEFAULT 0,
    published_asset_count BIGINT NOT NULL DEFAULT 0,
    publication_source VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    updated_by_platform_admin_id BIGINT NULL,
    change_reason VARCHAR(500) NOT NULL,
    started_at DATETIME(3) NOT NULL,
    completed_at DATETIME(3) NULL,
    updated_at DATETIME(3) NOT NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (singleton_id),
    CONSTRAINT ck_dev_fullness_policy_singleton CHECK (
        singleton_id = 1
    ),
    CONSTRAINT ck_dev_fullness_policy_version CHECK (
        policy_version BETWEEN 1 AND 9007199254740991
        AND lock_version >= 0
    ),
    CONSTRAINT ck_dev_fullness_policy_value CHECK (
        fullness_mode IN ('INFRARED_ONLY', 'WEIGHT_ONLY', 'INFRARED_OR_WEIGHT')
        AND fullness_weight_kg BETWEEN 0.001 AND 4294967.295
    ),
    CONSTRAINT ck_dev_fullness_policy_uid CHECK (
        rollout_uid = LOWER(rollout_uid)
        AND rollout_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_fullness_policy_rollout CHECK (
        rollout_status IN ('PENDING', 'RUNNING', 'DONE')
        AND next_asset_id >= 0
        AND target_asset_count >= 0
        AND processed_asset_count >= 0
        AND published_asset_count >= 0
        AND published_asset_count <= processed_asset_count
        AND (
            (rollout_status IN ('PENDING', 'RUNNING')
                AND completed_at IS NULL)
            OR
            (rollout_status = 'DONE' AND completed_at IS NOT NULL)
        )
    ),
    CONSTRAINT ck_dev_fullness_policy_publisher CHECK (
        (
            publication_source = 'SYSTEM'
            AND updated_by_platform_admin_id IS NULL
        )
        OR
        (
            publication_source = 'PLATFORM_ADMIN'
            AND updated_by_platform_admin_id IS NOT NULL
        )
    ),
    CONSTRAINT ck_dev_fullness_policy_reason CHECK (
        CHAR_LENGTH(TRIM(change_reason)) BETWEEN 1 AND 500
    ),
    CONSTRAINT ck_dev_fullness_policy_times CHECK (
        updated_at >= started_at
        AND (completed_at IS NULL OR completed_at >= started_at)
    ),
    CONSTRAINT fk_dev_fullness_policy_admin
        FOREIGN KEY (updated_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO dev_fullness_policy (
    singleton_id, policy_version, fullness_mode, fullness_weight_kg,
    rollout_uid, rollout_status, next_asset_id,
    target_asset_count, processed_asset_count, published_asset_count,
    publication_source, updated_by_platform_admin_id, change_reason,
    started_at, completed_at, updated_at, lock_version
) VALUES (
    1, 1, 'INFRARED_OR_WEIGHT', 50.000,
    '00000000-0000-4000-8000-000000000070', 'PENDING', 0,
    0, 0, 0,
    'SYSTEM', NULL, 'V70 统一满溢设置：50 千克或红外满足',
    UTC_TIMESTAMP(3), NULL, UTC_TIMESTAMP(3), 0
);

ALTER TABLE dev_config_version
    ADD COLUMN fullness_policy_version_no BIGINT NULL
        AFTER runtime_snapshot_policy_version_no,
    ADD CONSTRAINT ck_dev_config_fullness_policy_version CHECK (
        fullness_policy_version_no IS NULL
        OR fullness_policy_version_no
            BETWEEN 1 AND 9007199254740991
    ),
    ADD INDEX ix_dev_config_fullness_policy (
        asset_id,
        fullness_policy_version_no,
        version_no DESC
    );
