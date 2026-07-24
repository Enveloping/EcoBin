-- EcoBin P0 target migration epoch.
-- V1 owns the IAM core only. It deliberately contains no tenant, account,
-- credential, device, configuration, or other environment instance data.

CREATE TABLE iam_platform_admin (
    id BIGINT NOT NULL AUTO_INCREMENT,
    platform_admin_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    login_name VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    password_hash VARCHAR(255) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    enabled TINYINT NOT NULL,
    failed_login_count INT NOT NULL DEFAULT 0,
    locked_until DATETIME(3) NULL,
    auth_version BIGINT NOT NULL DEFAULT 0,
    password_changed_at DATETIME(3) NOT NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_iam_platform_admin_uid UNIQUE (platform_admin_uid),
    CONSTRAINT uq_iam_platform_admin_login UNIQUE (login_name),
    CONSTRAINT ck_iam_platform_admin_uid_v4 CHECK (
        platform_admin_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_iam_platform_admin_login_norm
        CHECK (login_name = LOWER(TRIM(login_name)) AND CHAR_LENGTH(login_name) > 0),
    CONSTRAINT ck_iam_platform_admin_enabled CHECK (enabled IN (0, 1)),
    CONSTRAINT ck_iam_platform_admin_counters
        CHECK (failed_login_count >= 0 AND auth_version >= 0 AND lock_version >= 0),
    CONSTRAINT ck_iam_platform_admin_times
        CHECK (password_changed_at >= created_at AND updated_at >= created_at),
    INDEX ix_iam_platform_admin_enabled (enabled, id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE iam_tenant (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_code VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    enterprise_name VARCHAR(200) NOT NULL,
    status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    contact_name VARCHAR(100) NULL,
    contact_phone VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    contact_address VARCHAR(500) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_iam_tenant_code UNIQUE (tenant_code),
    CONSTRAINT ck_iam_tenant_code_norm
        CHECK (tenant_code = LOWER(TRIM(tenant_code)) AND CHAR_LENGTH(tenant_code) > 0),
    CONSTRAINT ck_iam_tenant_status CHECK (status IN ('ENABLED', 'DISABLED')),
    CONSTRAINT ck_iam_tenant_lock_version CHECK (lock_version >= 0),
    CONSTRAINT ck_iam_tenant_times CHECK (updated_at >= created_at),
    INDEX ix_iam_tenant_status (status, id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE iam_organization (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_id BIGINT NOT NULL,
    organization_code VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    organization_name VARCHAR(200) NOT NULL,
    status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    contact_phone VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    contact_address VARCHAR(500) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_iam_org_tenant_code UNIQUE (tenant_id, organization_code),
    CONSTRAINT uq_iam_org_tenant_id UNIQUE (tenant_id, id),
    CONSTRAINT ck_iam_org_code_norm
        CHECK (organization_code = LOWER(TRIM(organization_code))
               AND CHAR_LENGTH(organization_code) > 0),
    CONSTRAINT ck_iam_org_status CHECK (status IN ('ENABLED', 'DISABLED')),
    CONSTRAINT ck_iam_org_lock_version CHECK (lock_version >= 0),
    CONSTRAINT ck_iam_org_times CHECK (updated_at >= created_at),
    CONSTRAINT fk_iam_org_tenant
        FOREIGN KEY (tenant_id) REFERENCES iam_tenant (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_iam_org_tenant_status (tenant_id, status, id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE iam_organization_miniapp (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    appid VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    login_enabled TINYINT NOT NULL,
    secret_ref VARCHAR(255) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    activated_at DATETIME(3) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    configured_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_iam_miniapp_appid UNIQUE (appid),
    CONSTRAINT uq_iam_miniapp_org UNIQUE (tenant_id, organization_id),
    CONSTRAINT uq_iam_miniapp_scope_id UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT ck_iam_miniapp_appid_nonblank
        CHECK (
            BINARY appid = BINARY TRIM(appid)
            AND CHAR_LENGTH(TRIM(appid)) > 0
        ),
    CONSTRAINT ck_iam_miniapp_login_enabled CHECK (login_enabled IN (0, 1)),
    CONSTRAINT ck_iam_miniapp_activation_shape CHECK (
        login_enabled = 0
        OR activated_at IS NOT NULL
    ),
    CONSTRAINT ck_iam_miniapp_secret_ref
        CHECK (CHAR_LENGTH(TRIM(secret_ref)) > 0),
    CONSTRAINT ck_iam_miniapp_lock_version CHECK (lock_version >= 0),
    CONSTRAINT ck_iam_miniapp_times
        CHECK (configured_at >= created_at
               AND updated_at >= created_at
               AND (activated_at IS NULL OR activated_at >= created_at)),
    CONSTRAINT fk_iam_miniapp_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_iam_miniapp_scope (tenant_id, organization_id, login_enabled, id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE iam_staff_account (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_id BIGINT NOT NULL,
    staff_account_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    account_kind VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    login_name VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    password_hash VARCHAR(255) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    contact_phone VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    enabled TINYINT NOT NULL,
    failed_login_count INT NOT NULL DEFAULT 0,
    locked_until DATETIME(3) NULL,
    auth_version BIGINT NOT NULL DEFAULT 0,
    password_changed_at DATETIME(3) NOT NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    principal_tenant_slot BIGINT
        GENERATED ALWAYS AS (
            CASE WHEN account_kind = 'TENANT_PRINCIPAL' THEN tenant_id ELSE NULL END
        ) STORED,
    PRIMARY KEY (id),
    CONSTRAINT uq_iam_staff_uid UNIQUE (staff_account_uid),
    CONSTRAINT uq_iam_staff_login UNIQUE (login_name),
    CONSTRAINT uq_iam_staff_principal_slot UNIQUE (principal_tenant_slot),
    CONSTRAINT uq_iam_staff_tenant_id UNIQUE (tenant_id, id),
    CONSTRAINT ck_iam_staff_uid_v4 CHECK (
        staff_account_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_iam_staff_kind
        CHECK (account_kind IN ('TENANT_PRINCIPAL', 'STAFF')),
    CONSTRAINT ck_iam_staff_login_norm
        CHECK (login_name = LOWER(TRIM(login_name)) AND CHAR_LENGTH(login_name) > 0),
    CONSTRAINT ck_iam_staff_enabled CHECK (enabled IN (0, 1)),
    CONSTRAINT ck_iam_staff_counters
        CHECK (failed_login_count >= 0 AND auth_version >= 0 AND lock_version >= 0),
    CONSTRAINT ck_iam_staff_times
        CHECK (password_changed_at >= created_at AND updated_at >= created_at),
    CONSTRAINT fk_iam_staff_tenant
        FOREIGN KEY (tenant_id) REFERENCES iam_tenant (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_iam_staff_tenant_list (tenant_id, enabled, account_kind, id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE iam_organization_staff_membership (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    staff_account_id BIGINT NOT NULL,
    is_manager TINYINT NOT NULL,
    enabled TINYINT NOT NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_iam_membership_org_staff
        UNIQUE (tenant_id, organization_id, staff_account_id),
    CONSTRAINT uq_iam_membership_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT ck_iam_membership_manager CHECK (is_manager IN (0, 1)),
    CONSTRAINT ck_iam_membership_enabled CHECK (enabled IN (0, 1)),
    CONSTRAINT ck_iam_membership_lock_version CHECK (lock_version >= 0),
    CONSTRAINT ck_iam_membership_times CHECK (updated_at >= created_at),
    CONSTRAINT fk_iam_membership_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_iam_membership_staff
        FOREIGN KEY (tenant_id, staff_account_id)
        REFERENCES iam_staff_account (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_iam_membership_staff_org
        (tenant_id, staff_account_id, enabled, organization_id),
    INDEX ix_iam_membership_org_manager
        (tenant_id, organization_id, is_manager, enabled, staff_account_id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE iam_permission_definition (
    id BIGINT NOT NULL AUTO_INCREMENT,
    permission_code VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    scope_kind VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    permission_name VARCHAR(100) NOT NULL,
    description VARCHAR(500) NULL,
    enabled TINYINT NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_iam_permission_code_scope UNIQUE (permission_code, scope_kind),
    CONSTRAINT uq_iam_permission_id_scope UNIQUE (id, scope_kind),
    CONSTRAINT ck_iam_permission_code_norm
        CHECK (permission_code = LOWER(TRIM(permission_code))
               AND CHAR_LENGTH(permission_code) > 0),
    CONSTRAINT ck_iam_permission_scope
        CHECK (scope_kind IN ('TENANT', 'ORGANIZATION')),
    CONSTRAINT ck_iam_permission_enabled CHECK (enabled IN (0, 1)),
    INDEX ix_iam_permission_scope (scope_kind, enabled, permission_code)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE iam_staff_permission_grant (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_id BIGINT NOT NULL,
    staff_account_id BIGINT NOT NULL,
    permission_definition_id BIGINT NOT NULL,
    scope_kind VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    organization_id BIGINT NULL,
    granted_at DATETIME(3) NOT NULL,
    revoked_at DATETIME(3) NULL,
    scope_organization_key VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin
        GENERATED ALWAYS AS (
            CASE
                WHEN scope_kind = 'TENANT' THEN 'T'
                WHEN scope_kind = 'ORGANIZATION'
                    THEN CONCAT('O:', CAST(organization_id AS CHAR))
                ELSE NULL
            END
        ) STORED,
    active_marker CHAR(1) CHARACTER SET ascii COLLATE ascii_bin
        GENERATED ALWAYS AS (
            CASE WHEN revoked_at IS NULL THEN 'A' ELSE NULL END
        ) STORED,
    PRIMARY KEY (id),
    CONSTRAINT uq_iam_grant_active UNIQUE (
        tenant_id,
        staff_account_id,
        permission_definition_id,
        scope_organization_key,
        active_marker
    ),
    CONSTRAINT ck_iam_grant_scope_shape
        CHECK (
            (scope_kind = 'TENANT' AND organization_id IS NULL)
            OR
            (scope_kind = 'ORGANIZATION' AND organization_id IS NOT NULL)
        ),
    CONSTRAINT ck_iam_grant_times
        CHECK (revoked_at IS NULL OR revoked_at >= granted_at),
    CONSTRAINT fk_iam_grant_staff
        FOREIGN KEY (tenant_id, staff_account_id)
        REFERENCES iam_staff_account (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_iam_grant_permission
        FOREIGN KEY (permission_definition_id, scope_kind)
        REFERENCES iam_permission_definition (id, scope_kind)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_iam_grant_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_iam_grant_membership
        FOREIGN KEY (tenant_id, organization_id, staff_account_id)
        REFERENCES iam_organization_staff_membership (
            tenant_id,
            organization_id,
            staff_account_id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_iam_grant_staff_active (
        tenant_id,
        staff_account_id,
        active_marker,
        scope_kind,
        organization_id
    ),
    INDEX ix_iam_grant_org_fk (tenant_id, organization_id),
    INDEX ix_iam_grant_permission_fk (
        permission_definition_id,
        scope_kind
    ),
    INDEX ix_iam_grant_membership_fk (
        tenant_id,
        organization_id,
        staff_account_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
