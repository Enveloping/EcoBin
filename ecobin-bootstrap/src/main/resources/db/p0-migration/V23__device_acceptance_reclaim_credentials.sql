-- Immutable commissioning evidence, platform reclaim evidence and OneNet
-- device credential rotation attestations. Credential values are deliberately
-- absent from this schema.

CREATE TABLE dev_deployment_acceptance (
    id BIGINT NOT NULL AUTO_INCREMENT,
    acceptance_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    config_version_id BIGINT NOT NULL,
    config_version_no BIGINT NOT NULL,
    runtime_edge_event_id BIGINT NOT NULL,
    runtime_received_at DATETIME(3) NOT NULL,
    evidence_json JSON NOT NULL,
    delivery_door_observed_normal TINYINT NOT NULL,
    cameras_observed_normal TINYINT NOT NULL,
    clean_door_installation_observed_normal TINYINT NOT NULL,
    accepted_by_platform_admin_id BIGINT NOT NULL,
    acceptance_reason VARCHAR(500) NULL,
    accepted_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_deployment_acceptance_uid UNIQUE (acceptance_uid),
    CONSTRAINT uq_dev_deployment_acceptance_operation
        UNIQUE (deployment_id, acceptance_uid),
    CONSTRAINT ck_dev_deployment_acceptance_uid CHECK (
        acceptance_uid = LOWER(acceptance_uid)
        AND acceptance_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_deployment_acceptance_manual CHECK (
        delivery_door_observed_normal = 1
        AND cameras_observed_normal = 1
        AND clean_door_installation_observed_normal = 1
    ),
    CONSTRAINT ck_dev_deployment_acceptance_times CHECK (
        accepted_at >= runtime_received_at
        AND created_at = accepted_at
        AND (acceptance_reason IS NULL
             OR CHAR_LENGTH(TRIM(acceptance_reason)) > 0)
    ),
    CONSTRAINT fk_dev_deployment_acceptance_deployment
        FOREIGN KEY (tenant_id, organization_id, deployment_id)
        REFERENCES dev_device_deployment (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_deployment_acceptance_config
        FOREIGN KEY (config_version_id)
        REFERENCES dev_config_version (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_deployment_acceptance_actor
        FOREIGN KEY (accepted_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_deployment_acceptance_history (
        tenant_id, organization_id, deployment_id, accepted_at, id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_onenet_credential_rotation_confirmation (
    id BIGINT NOT NULL AUTO_INCREMENT,
    confirmation_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    asset_id BIGINT NOT NULL,
    confirmed_by_platform_admin_id BIGINT NOT NULL,
    confirmation_reason VARCHAR(500) NOT NULL,
    confirmed_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_onenet_rotation_uid UNIQUE (confirmation_uid),
    CONSTRAINT ck_dev_onenet_rotation_uid CHECK (
        confirmation_uid = LOWER(confirmation_uid)
        AND confirmation_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_onenet_rotation_reason CHECK (
        CHAR_LENGTH(TRIM(confirmation_reason)) > 0
    ),
    CONSTRAINT ck_dev_onenet_rotation_times CHECK (
        created_at = confirmed_at
    ),
    CONSTRAINT fk_dev_onenet_rotation_asset
        FOREIGN KEY (asset_id) REFERENCES dev_device_asset (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_onenet_rotation_actor
        FOREIGN KEY (confirmed_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_onenet_rotation_asset (
        asset_id, confirmed_at, id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_asset_reclaim_record (
    id BIGINT NOT NULL AUTO_INCREMENT,
    reclaim_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    allocation_id BIGINT NOT NULL,
    tenant_id BIGINT NOT NULL,
    asset_id BIGINT NOT NULL,
    deployment_id BIGINT NULL,
    reclaim_mode VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    physical_possession_confirmed TINYINT NOT NULL,
    blocker_snapshot_json JSON NOT NULL,
    reclaim_reason VARCHAR(500) NOT NULL,
    reclaimed_by_platform_admin_id BIGINT NOT NULL,
    reclaimed_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_asset_reclaim_uid UNIQUE (reclaim_uid),
    CONSTRAINT uq_dev_asset_reclaim_allocation UNIQUE (allocation_id),
    CONSTRAINT ck_dev_asset_reclaim_uid CHECK (
        reclaim_uid = LOWER(reclaim_uid)
        AND reclaim_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_asset_reclaim_mode CHECK (
        reclaim_mode IN ('NORMAL', 'EXCEPTIONAL')
    ),
    CONSTRAINT ck_dev_asset_reclaim_normal CHECK (
        reclaim_mode = 'EXCEPTIONAL'
        OR physical_possession_confirmed = 1
    ),
    CONSTRAINT ck_dev_asset_reclaim_reason CHECK (
        CHAR_LENGTH(TRIM(reclaim_reason)) > 0
        AND created_at = reclaimed_at
    ),
    CONSTRAINT fk_dev_asset_reclaim_allocation
        FOREIGN KEY (allocation_id, tenant_id, asset_id)
        REFERENCES dev_asset_tenant_allocation (id, tenant_id, asset_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_asset_reclaim_deployment
        FOREIGN KEY (deployment_id)
        REFERENCES dev_device_deployment (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_asset_reclaim_actor
        FOREIGN KEY (reclaimed_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_asset_reclaim_asset (
        asset_id, reclaimed_at, id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_asset_maintenance_clearance (
    id BIGINT NOT NULL AUTO_INCREMENT,
    clearance_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    asset_id BIGINT NOT NULL,
    physical_possession_confirmed TINYINT NOT NULL,
    inspection_confirmed TINYINT NOT NULL,
    clearance_reason VARCHAR(500) NOT NULL,
    cleared_by_platform_admin_id BIGINT NOT NULL,
    cleared_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_asset_clearance_uid UNIQUE (clearance_uid),
    CONSTRAINT ck_dev_asset_clearance_uid CHECK (
        clearance_uid = LOWER(clearance_uid)
        AND clearance_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_asset_clearance_confirmation CHECK (
        physical_possession_confirmed = 1
        AND inspection_confirmed = 1
        AND CHAR_LENGTH(TRIM(clearance_reason)) > 0
        AND created_at = cleared_at
    ),
    CONSTRAINT fk_dev_asset_clearance_asset
        FOREIGN KEY (asset_id) REFERENCES dev_device_asset (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_asset_clearance_actor
        FOREIGN KEY (cleared_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_asset_clearance_asset (asset_id, cleared_at, id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
