-- Two-stage device ownership: platform inventory -> tenant pool -> organization
-- deployment. Public UUIDs address records; all relationships remain internal
-- BIGINT foreign keys.

CREATE TABLE dev_asset_tenant_allocation (
    id BIGINT NOT NULL AUTO_INCREMENT,
    allocation_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    asset_id BIGINT NOT NULL,
    allocation_source VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    allocated_by_platform_admin_id BIGINT NULL,
    allocated_at DATETIME(3) NOT NULL,
    ended_by_platform_admin_id BIGINT NULL,
    ended_at DATETIME(3) NULL,
    end_mode VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NULL,
    end_reason VARCHAR(500) NULL,
    legacy_deployment_id BIGINT NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_tenant_allocation_uid UNIQUE (allocation_uid),
    CONSTRAINT uq_dev_tenant_allocation_legacy UNIQUE (legacy_deployment_id),
    CONSTRAINT uq_dev_tenant_allocation_scope_id UNIQUE (tenant_id, id),
    CONSTRAINT uq_dev_tenant_allocation_asset_id UNIQUE (asset_id, id),
    CONSTRAINT uq_dev_tenant_allocation_relation
        UNIQUE (id, tenant_id, asset_id),
    CONSTRAINT ck_dev_tenant_allocation_uid CHECK (
        allocation_uid = LOWER(allocation_uid)
        AND allocation_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_tenant_allocation_source CHECK (
        allocation_source IN ('PLATFORM_ASSIGNMENT', 'LEGACY_DIRECT_DEPLOYMENT')
    ),
    CONSTRAINT ck_dev_tenant_allocation_status CHECK (
        status IN ('ACTIVE', 'ENDED')
    ),
    CONSTRAINT ck_dev_tenant_allocation_actor CHECK (
        allocation_source = 'LEGACY_DIRECT_DEPLOYMENT'
        OR allocated_by_platform_admin_id IS NOT NULL
    ),
    CONSTRAINT ck_dev_tenant_allocation_end CHECK (
        (
            status = 'ACTIVE'
            AND ended_at IS NULL
            AND ended_by_platform_admin_id IS NULL
            AND end_mode IS NULL
            AND end_reason IS NULL
        )
        OR
        (
            status = 'ENDED'
            AND ended_at IS NOT NULL
            AND end_mode IN ('NORMAL', 'EXCEPTIONAL', 'LEGACY')
            AND (end_reason IS NULL OR CHAR_LENGTH(TRIM(end_reason)) > 0)
        )
    ),
    CONSTRAINT ck_dev_tenant_allocation_version CHECK (lock_version >= 0),
    CONSTRAINT ck_dev_tenant_allocation_times CHECK (
        updated_at >= created_at
        AND allocated_at >= created_at
        AND (ended_at IS NULL OR ended_at >= allocated_at)
    ),
    CONSTRAINT fk_dev_tenant_allocation_tenant
        FOREIGN KEY (tenant_id) REFERENCES iam_tenant (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_tenant_allocation_asset
        FOREIGN KEY (asset_id) REFERENCES dev_device_asset (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_tenant_allocation_allocator
        FOREIGN KEY (allocated_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_tenant_allocation_ender
        FOREIGN KEY (ended_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_tenant_allocation_tenant (
        tenant_id, status, allocated_at, id
    ),
    INDEX ix_dev_tenant_allocation_asset (
        asset_id, allocated_at, id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_asset_active_tenant_allocation (
    asset_id BIGINT NOT NULL,
    tenant_id BIGINT NOT NULL,
    allocation_id BIGINT NOT NULL,
    acquired_at DATETIME(3) NOT NULL,
    PRIMARY KEY (asset_id),
    CONSTRAINT uq_dev_active_tenant_allocation UNIQUE (allocation_id),
    CONSTRAINT fk_dev_active_tenant_allocation_asset
        FOREIGN KEY (asset_id) REFERENCES dev_device_asset (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_active_tenant_allocation_tenant
        FOREIGN KEY (tenant_id) REFERENCES iam_tenant (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_active_tenant_allocation_relation
        FOREIGN KEY (allocation_id, tenant_id, asset_id)
        REFERENCES dev_asset_tenant_allocation (id, tenant_id, asset_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_active_tenant_allocation_tenant (
        tenant_id, allocation_id, asset_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE dev_device_deployment
    ADD COLUMN tenant_allocation_id BIGINT NULL AFTER asset_id,
    ADD COLUMN predecessor_deployment_id BIGINT NULL AFTER tenant_allocation_id,
    ADD COLUMN readiness_mode VARCHAR(40)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL
        DEFAULT 'LEGACY_DIRECT_DEPLOYMENT' AFTER predecessor_deployment_id,
    ADD CONSTRAINT ck_dev_deployment_readiness_mode CHECK (
        readiness_mode IN (
            'PLATFORM_ACCEPTANCE_REQUIRED',
            'AUTOMATIC_TRANSFER_READINESS',
            'LEGACY_DIRECT_DEPLOYMENT'
        )
    ),
    ADD CONSTRAINT fk_dev_deployment_predecessor
        FOREIGN KEY (predecessor_deployment_id)
        REFERENCES dev_device_deployment (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT;

-- The old direct-deployment implementation had no tenant allocation fact.
-- Preserve that uncertainty explicitly instead of inventing a continuous
-- ownership history.
INSERT INTO dev_asset_tenant_allocation (
    allocation_uid, tenant_id, asset_id, allocation_source, status,
    allocated_by_platform_admin_id, allocated_at,
    ended_by_platform_admin_id, ended_at, end_mode, end_reason,
    legacy_deployment_id, lock_version, created_at, updated_at
)
SELECT LOWER(UUID()), d.tenant_id, d.asset_id,
       'LEGACY_DIRECT_DEPLOYMENT',
       CASE WHEN active.deployment_id IS NULL THEN 'ENDED' ELSE 'ACTIVE' END,
       NULL, d.created_at,
       NULL,
       CASE WHEN active.deployment_id IS NULL
            THEN COALESCE(d.ended_at, d.updated_at)
            ELSE NULL END,
       CASE WHEN active.deployment_id IS NULL THEN 'LEGACY' ELSE NULL END,
       CASE WHEN active.deployment_id IS NULL
            THEN 'backfilled from legacy direct deployment'
            ELSE NULL END,
       d.id, 0, d.created_at,
       CASE WHEN active.deployment_id IS NULL
            THEN COALESCE(d.ended_at, d.updated_at)
            ELSE d.updated_at END
FROM dev_device_deployment d
LEFT JOIN dev_asset_active_deployment active
  ON active.deployment_id = d.id;

UPDATE dev_device_deployment deployment
JOIN dev_asset_tenant_allocation allocation
  ON allocation.legacy_deployment_id = deployment.id
SET deployment.tenant_allocation_id = allocation.id;

INSERT INTO dev_asset_active_tenant_allocation (
    asset_id, tenant_id, allocation_id, acquired_at
)
SELECT active.asset_id, active.tenant_id, allocation.id, active.acquired_at
FROM dev_asset_active_deployment active
JOIN dev_asset_tenant_allocation allocation
  ON allocation.legacy_deployment_id = active.deployment_id;

ALTER TABLE dev_device_deployment
    MODIFY COLUMN tenant_allocation_id BIGINT NOT NULL,
    ADD CONSTRAINT fk_dev_deployment_tenant_allocation
        FOREIGN KEY (tenant_allocation_id, tenant_id, asset_id)
        REFERENCES dev_asset_tenant_allocation (id, tenant_id, asset_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_dev_deployment_tenant_allocation (
        tenant_allocation_id, tenant_id, asset_id, created_at, id
    );

INSERT INTO iam_permission_definition (
    permission_code, scope_kind, permission_name,
    description, enabled, created_at
) VALUES
    (
        'device.allocation.manage', 'TENANT', '管理租户设备分配',
        '将本租户设备池中的设备分配到机构、结束部署和执行租户内调拨。',
        1, '2026-08-01 00:00:00.000'
    ),
    (
        'device.business.manage', 'TENANT', '管理设备经营状态',
        '由租户总部决定本租户设备开始经营或停止经营。',
        1, '2026-08-01 00:00:00.000'
    );
