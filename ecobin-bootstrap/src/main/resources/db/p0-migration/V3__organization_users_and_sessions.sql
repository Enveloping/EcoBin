-- Organization-scoped mini-program identities, staff bindings, capabilities,
-- and the three strongly typed session families.

CREATE TABLE iam_organization_user (
    id BIGINT NOT NULL AUTO_INCREMENT,
    organization_user_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    organization_miniapp_id BIGINT NOT NULL,
    openid VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    phone_e164 VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NULL,
    phone_bound_at DATETIME(3) NULL,
    nickname VARCHAR(100) NULL,
    avatar_url VARCHAR(1000) CHARACTER SET ascii COLLATE ascii_bin NULL,
    status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    auth_version BIGINT NOT NULL DEFAULT 0,
    lock_version BIGINT NOT NULL DEFAULT 0,
    registered_at DATETIME(3) NOT NULL,
    registered_via_deployment_id BIGINT NULL,
    frozen_at DATETIME(3) NULL,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_iam_org_user_uid UNIQUE (organization_user_uid),
    CONSTRAINT uq_iam_org_user_appid_openid
        UNIQUE (organization_miniapp_id, openid),
    CONSTRAINT uq_iam_org_user_scope_phone
        UNIQUE (tenant_id, organization_id, phone_e164),
    CONSTRAINT uq_iam_org_user_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_iam_org_user_scope_app_id
        UNIQUE (
            tenant_id,
            organization_id,
            organization_miniapp_id,
            id
        ),
    CONSTRAINT ck_iam_org_user_uid_v4 CHECK (
        organization_user_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_iam_org_user_openid_nonblank
        CHECK (
            BINARY openid = BINARY TRIM(openid)
            AND CHAR_LENGTH(TRIM(openid)) > 0
        ),
    CONSTRAINT ck_iam_org_user_phone_shape CHECK (
        (phone_e164 IS NULL AND phone_bound_at IS NULL)
        OR
        (
            phone_e164 IS NOT NULL
            AND phone_e164 REGEXP '^\\+[1-9][0-9]{1,14}$'
            AND phone_bound_at IS NOT NULL
        )
    ),
    CONSTRAINT ck_iam_org_user_status CHECK (status IN ('ACTIVE', 'FROZEN')),
    CONSTRAINT ck_iam_org_user_frozen_shape CHECK (
        (status = 'ACTIVE' AND frozen_at IS NULL)
        OR
        (status = 'FROZEN' AND frozen_at IS NOT NULL)
    ),
    CONSTRAINT ck_iam_org_user_versions
        CHECK (auth_version >= 0 AND lock_version >= 0),
    CONSTRAINT ck_iam_org_user_times CHECK (
        registered_at >= created_at
        AND updated_at >= created_at
        AND (phone_bound_at IS NULL OR phone_bound_at >= registered_at)
        AND (frozen_at IS NULL OR frozen_at >= registered_at)
    ),
    CONSTRAINT fk_iam_org_user_organization
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_iam_org_user_miniapp
        FOREIGN KEY (
            tenant_id,
            organization_id,
            organization_miniapp_id
        )
        REFERENCES iam_organization_miniapp (
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_iam_org_user_registration_deployment
        FOREIGN KEY (
            tenant_id,
            organization_id,
            registered_via_deployment_id
        )
        REFERENCES dev_device_deployment (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_iam_org_user_scope_list (
        tenant_id,
        organization_id,
        status,
        registered_at,
        id
    ),
    INDEX ix_iam_org_user_registration (
        tenant_id,
        organization_id,
        registered_via_deployment_id,
        registered_at
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE iam_organization_user_capability (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    organization_user_id BIGINT NOT NULL,
    capability_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    enabled TINYINT NOT NULL,
    granted_at DATETIME(3) NOT NULL,
    revoked_at DATETIME(3) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_iam_org_user_capability
        UNIQUE (
            tenant_id,
            organization_id,
            organization_user_id,
            capability_code
        ),
    CONSTRAINT ck_iam_org_user_cap_code CHECK (
        capability_code = UPPER(TRIM(capability_code))
        AND CHAR_LENGTH(capability_code) > 0
    ),
    CONSTRAINT ck_iam_org_user_cap_enabled CHECK (enabled IN (0, 1)),
    CONSTRAINT ck_iam_org_user_cap_state CHECK (
        (enabled = 1 AND revoked_at IS NULL)
        OR
        (enabled = 0 AND revoked_at IS NOT NULL)
    ),
    CONSTRAINT ck_iam_org_user_cap_times CHECK (
        updated_at >= granted_at
        AND (revoked_at IS NULL OR revoked_at >= granted_at)
    ),
    CONSTRAINT ck_iam_org_user_cap_lock CHECK (lock_version >= 0),
    CONSTRAINT fk_iam_org_user_cap_organization
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_iam_org_user_cap_user
        FOREIGN KEY (tenant_id, organization_id, organization_user_id)
        REFERENCES iam_organization_user (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_iam_org_user_cap_active (
        tenant_id,
        organization_id,
        capability_code,
        enabled,
        organization_user_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE iam_staff_miniapp_binding (
    id BIGINT NOT NULL AUTO_INCREMENT,
    binding_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    organization_miniapp_id BIGINT NOT NULL,
    organization_user_id BIGINT NOT NULL,
    staff_account_id BIGINT NOT NULL,
    status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    bound_at DATETIME(3) NOT NULL,
    revoked_at DATETIME(3) NULL,
    revocation_reason VARCHAR(500) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    active_organization_user_id BIGINT
        GENERATED ALWAYS AS (
            CASE WHEN status = 'ACTIVE' THEN organization_user_id ELSE NULL END
        ) STORED,
    active_staff_account_id BIGINT
        GENERATED ALWAYS AS (
            CASE WHEN status = 'ACTIVE' THEN staff_account_id ELSE NULL END
        ) STORED,
    PRIMARY KEY (id),
    CONSTRAINT uq_iam_staff_binding_uid UNIQUE (binding_uid),
    CONSTRAINT uq_iam_staff_binding_active_user
        UNIQUE (active_organization_user_id),
    CONSTRAINT uq_iam_staff_binding_active_staff_app
        UNIQUE (organization_miniapp_id, active_staff_account_id),
    CONSTRAINT uq_iam_staff_binding_session_ref
        UNIQUE (
            tenant_id,
            organization_id,
            organization_miniapp_id,
            staff_account_id,
            id
        ),
    CONSTRAINT ck_iam_staff_binding_uid_v4 CHECK (
        binding_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_iam_staff_binding_status
        CHECK (status IN ('ACTIVE', 'REVOKED')),
    CONSTRAINT ck_iam_staff_binding_revoked_shape CHECK (
        (
            status = 'ACTIVE'
            AND revoked_at IS NULL
            AND revocation_reason IS NULL
        )
        OR
        (
            status = 'REVOKED'
            AND revoked_at IS NOT NULL
            AND (
                revocation_reason IS NULL
                OR CHAR_LENGTH(TRIM(revocation_reason)) > 0
            )
        )
    ),
    CONSTRAINT ck_iam_staff_binding_times CHECK (
        bound_at >= created_at
        AND (revoked_at IS NULL OR revoked_at >= bound_at)
    ),
    CONSTRAINT ck_iam_staff_binding_lock CHECK (lock_version >= 0),
    CONSTRAINT fk_iam_staff_binding_organization
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_iam_staff_binding_user
        FOREIGN KEY (
            tenant_id,
            organization_id,
            organization_miniapp_id,
            organization_user_id
        )
        REFERENCES iam_organization_user (
            tenant_id,
            organization_id,
            organization_miniapp_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_iam_staff_binding_staff
        FOREIGN KEY (tenant_id, staff_account_id)
        REFERENCES iam_staff_account (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_iam_staff_binding_staff_app (
        tenant_id,
        staff_account_id,
        organization_miniapp_id,
        status,
        id
    ),
    INDEX ix_iam_staff_binding_org_user (
        tenant_id,
        organization_id,
        organization_user_id,
        status,
        id
    ),
    INDEX ix_iam_staff_binding_user_fk (
        tenant_id,
        organization_id,
        organization_miniapp_id,
        organization_user_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE iam_platform_login_session (
    id BIGINT NOT NULL AUTO_INCREMENT,
    session_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    platform_admin_id BIGINT NOT NULL,
    issued_at DATETIME(3) NOT NULL,
    expires_at DATETIME(3) NOT NULL,
    revoked_at DATETIME(3) NULL,
    revocation_reason VARCHAR(500) NULL,
    login_ip VARBINARY(16) NULL,
    user_agent_sha256 BINARY(32) NULL,
    auth_version_snapshot BIGINT NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_iam_platform_session_uid UNIQUE (session_uid),
    CONSTRAINT ck_iam_platform_session_uid_v4 CHECK (
        session_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_iam_platform_session_times CHECK (
        issued_at >= created_at
        AND expires_at > issued_at
        AND (revoked_at IS NULL OR revoked_at >= issued_at)
    ),
    CONSTRAINT ck_iam_platform_session_revoked CHECK (
        (revoked_at IS NULL AND revocation_reason IS NULL)
        OR
        (
            revoked_at IS NOT NULL
            AND (
                revocation_reason IS NULL
                OR CHAR_LENGTH(TRIM(revocation_reason)) > 0
            )
        )
    ),
    CONSTRAINT ck_iam_platform_session_auth CHECK (auth_version_snapshot >= 0),
    CONSTRAINT fk_iam_platform_session_admin
        FOREIGN KEY (platform_admin_id) REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_iam_platform_session_principal (
        platform_admin_id,
        revoked_at,
        expires_at,
        id
    ),
    INDEX ix_iam_platform_session_expiry (expires_at, id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE iam_staff_login_session (
    id BIGINT NOT NULL AUTO_INCREMENT,
    session_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    staff_account_id BIGINT NOT NULL,
    client_kind VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    staff_miniapp_binding_id BIGINT NULL,
    organization_miniapp_id BIGINT NULL,
    active_organization_id BIGINT NULL,
    issued_at DATETIME(3) NOT NULL,
    expires_at DATETIME(3) NOT NULL,
    revoked_at DATETIME(3) NULL,
    revocation_reason VARCHAR(500) NULL,
    login_ip VARBINARY(16) NULL,
    user_agent_sha256 BINARY(32) NULL,
    auth_version_snapshot BIGINT NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_iam_staff_session_uid UNIQUE (session_uid),
    CONSTRAINT ck_iam_staff_session_uid_v4 CHECK (
        session_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_iam_staff_session_client_shape CHECK (
        (
            client_kind = 'WEB'
            AND staff_miniapp_binding_id IS NULL
            AND organization_miniapp_id IS NULL
            AND active_organization_id IS NULL
        )
        OR
        (
            client_kind = 'MINIAPP_MANAGEMENT'
            AND staff_miniapp_binding_id IS NOT NULL
            AND organization_miniapp_id IS NOT NULL
            AND active_organization_id IS NOT NULL
        )
    ),
    CONSTRAINT ck_iam_staff_session_times CHECK (
        issued_at >= created_at
        AND expires_at > issued_at
        AND (revoked_at IS NULL OR revoked_at >= issued_at)
    ),
    CONSTRAINT ck_iam_staff_session_revoked CHECK (
        (revoked_at IS NULL AND revocation_reason IS NULL)
        OR
        (
            revoked_at IS NOT NULL
            AND (
                revocation_reason IS NULL
                OR CHAR_LENGTH(TRIM(revocation_reason)) > 0
            )
        )
    ),
    CONSTRAINT ck_iam_staff_session_auth CHECK (auth_version_snapshot >= 0),
    CONSTRAINT fk_iam_staff_session_staff
        FOREIGN KEY (tenant_id, staff_account_id)
        REFERENCES iam_staff_account (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_iam_staff_session_binding
        FOREIGN KEY (
            tenant_id,
            active_organization_id,
            organization_miniapp_id,
            staff_account_id,
            staff_miniapp_binding_id
        )
        REFERENCES iam_staff_miniapp_binding (
            tenant_id,
            organization_id,
            organization_miniapp_id,
            staff_account_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_iam_staff_session_principal (
        tenant_id,
        staff_account_id,
        revoked_at,
        expires_at,
        id
    ),
    INDEX ix_iam_staff_session_binding (
        staff_miniapp_binding_id,
        revoked_at,
        expires_at,
        id
    ),
    INDEX ix_iam_staff_session_expiry (expires_at, id),
    INDEX ix_iam_staff_session_binding_fk (
        tenant_id,
        active_organization_id,
        organization_miniapp_id,
        staff_account_id,
        staff_miniapp_binding_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE iam_organization_user_session (
    id BIGINT NOT NULL AUTO_INCREMENT,
    session_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    organization_miniapp_id BIGINT NOT NULL,
    organization_user_id BIGINT NOT NULL,
    issued_at DATETIME(3) NOT NULL,
    expires_at DATETIME(3) NOT NULL,
    revoked_at DATETIME(3) NULL,
    revocation_reason VARCHAR(500) NULL,
    login_ip VARBINARY(16) NULL,
    user_agent_sha256 BINARY(32) NULL,
    auth_version_snapshot BIGINT NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_iam_org_user_session_uid UNIQUE (session_uid),
    CONSTRAINT ck_iam_org_user_session_uid_v4 CHECK (
        session_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_iam_org_user_session_times CHECK (
        issued_at >= created_at
        AND expires_at > issued_at
        AND (revoked_at IS NULL OR revoked_at >= issued_at)
    ),
    CONSTRAINT ck_iam_org_user_session_revoked CHECK (
        (revoked_at IS NULL AND revocation_reason IS NULL)
        OR
        (
            revoked_at IS NOT NULL
            AND (
                revocation_reason IS NULL
                OR CHAR_LENGTH(TRIM(revocation_reason)) > 0
            )
        )
    ),
    CONSTRAINT ck_iam_org_user_session_auth CHECK (auth_version_snapshot >= 0),
    CONSTRAINT fk_iam_org_user_session_organization
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_iam_org_user_session_user
        FOREIGN KEY (
            tenant_id,
            organization_id,
            organization_miniapp_id,
            organization_user_id
        )
        REFERENCES iam_organization_user (
            tenant_id,
            organization_id,
            organization_miniapp_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_iam_org_user_session_principal (
        tenant_id,
        organization_id,
        organization_user_id,
        revoked_at,
        expires_at,
        id
    ),
    INDEX ix_iam_org_user_session_expiry (expires_at, id),
    INDEX ix_iam_org_user_session_user_fk (
        tenant_id,
        organization_id,
        organization_miniapp_id,
        organization_user_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
