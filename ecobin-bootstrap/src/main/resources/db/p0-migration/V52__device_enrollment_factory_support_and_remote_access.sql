-- V52: power-loss-safe device enrollment, factory bag admission and
-- short-lived, audited remote-support access.

ALTER TABLE dev_device_asset
    ADD COLUMN registration_source VARCHAR(24)
        CHARACTER SET ascii COLLATE ascii_bin
        NOT NULL DEFAULT 'PLATFORM_MANUAL'
        AFTER production_batch,
    ADD COLUMN factory_bag_revision BIGINT UNSIGNED NOT NULL DEFAULT 0
        AFTER registration_source,
    ADD COLUMN factory_bag_set_sha256 BINARY(32) NULL
        AFTER factory_bag_revision,
    ADD CONSTRAINT ck_dev_asset_registration_source_v52 CHECK (
        registration_source IN (
            'PLATFORM_MANUAL',
            'SELF_ENROLLMENT',
            'LEGACY_ADOPTION'
        )
    ),
    ADD CONSTRAINT ck_dev_asset_factory_bag_revision_v52 CHECK (
        factory_bag_revision >= 0
    );

CREATE TABLE dev_device_enrollment_challenge (
    id BIGINT NOT NULL AUTO_INCREMENT,
    challenge_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    enrollment_key_id VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    nonce BINARY(32) NOT NULL,
    request_ip_sha256 BINARY(32) NULL,
    status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    expires_at DATETIME(3) NOT NULL,
    consumed_at DATETIME(3) NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_enrollment_challenge_uid UNIQUE (challenge_uid),
    CONSTRAINT uq_dev_enrollment_challenge_nonce UNIQUE (nonce),
    CONSTRAINT ck_dev_enrollment_challenge_uid_v4 CHECK (
        challenge_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_enrollment_challenge_key CHECK (
        enrollment_key_id REGEXP '^[A-Z0-9_-]{1,16}$'
    ),
    CONSTRAINT ck_dev_enrollment_challenge_status CHECK (
        status IN ('ISSUED', 'CONSUMED', 'EXPIRED')
        AND (
            (status = 'ISSUED' AND consumed_at IS NULL)
            OR (status = 'CONSUMED' AND consumed_at IS NOT NULL)
            OR status = 'EXPIRED'
        )
    ),
    CONSTRAINT ck_dev_enrollment_challenge_times CHECK (
        expires_at > created_at
        AND (consumed_at IS NULL OR consumed_at >= created_at)
    ),
    INDEX ix_dev_enrollment_challenge_expiry (status, expires_at, id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_device_enrollment (
    id BIGINT NOT NULL AUTO_INCREMENT,
    enrollment_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    challenge_id BIGINT NOT NULL,
    enrollment_mode VARCHAR(24)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    hardware_sn VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    identity_public_key VARCHAR(128)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    identity_fingerprint_sha256 BINARY(32) NOT NULL,
    response_wrap_public_key BINARY(32) NOT NULL,
    tunnel_public_key VARCHAR(128)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tunnel_fingerprint_sha256 BINARY(32) NOT NULL,
    ssh_host_public_key VARCHAR(128)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    ssh_host_fingerprint_sha256 BINARY(32) NOT NULL,
    request_sha256 BINARY(32) NOT NULL,
    request_signature VARBINARY(64) NOT NULL,
    legacy_proof BINARY(32) NULL,
    status VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    asset_id BIGINT NULL,
    onenet_device_id VARCHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    encrypted_response MEDIUMBLOB NULL,
    response_nonce BINARY(12) NULL,
    response_sha256 BINARY(32) NULL,
    failure_code VARCHAR(100)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    attempt_count INT NOT NULL DEFAULT 0,
    next_attempt_at DATETIME(3) NULL,
    completed_at DATETIME(3) NULL,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_enrollment_uid UNIQUE (enrollment_uid),
    CONSTRAINT uq_dev_enrollment_challenge UNIQUE (challenge_id),
    CONSTRAINT uq_dev_enrollment_hardware_sn UNIQUE (hardware_sn),
    CONSTRAINT uq_dev_enrollment_identity UNIQUE (identity_fingerprint_sha256),
    CONSTRAINT uq_dev_enrollment_tunnel_key UNIQUE (tunnel_fingerprint_sha256),
    CONSTRAINT ck_dev_enrollment_uid_v4 CHECK (
        enrollment_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_enrollment_mode CHECK (
        enrollment_mode IN ('SELF_ENROLLMENT', 'LEGACY_ADOPTION')
    ),
    CONSTRAINT ck_dev_enrollment_hardware_sn CHECK (
        hardware_sn REGEXP '^[A-Za-z0-9_-]{8,64}$'
    ),
    CONSTRAINT ck_dev_enrollment_public_keys CHECK (
        identity_public_key REGEXP '^ssh-ed25519 [A-Za-z0-9+/]{68}$'
        AND tunnel_public_key REGEXP '^ssh-ed25519 [A-Za-z0-9+/]{68}$'
        AND ssh_host_public_key REGEXP '^ssh-ed25519 [A-Za-z0-9+/]{68}$'
    ),
    CONSTRAINT ck_dev_enrollment_status CHECK (
        status IN ('PENDING', 'PROVISIONING', 'READY', 'FAILED')
        AND (
            (
                status = 'READY'
                AND asset_id IS NOT NULL
                AND onenet_device_id IS NOT NULL
                AND encrypted_response IS NOT NULL
                AND response_nonce IS NOT NULL
                AND response_sha256 IS NOT NULL
                AND failure_code IS NULL
                AND completed_at IS NOT NULL
            )
            OR (
                status = 'FAILED'
                AND encrypted_response IS NULL
                AND response_nonce IS NULL
                AND response_sha256 IS NULL
                AND failure_code IS NOT NULL
            )
            OR status IN ('PENDING', 'PROVISIONING')
        )
    ),
    CONSTRAINT ck_dev_enrollment_legacy_proof CHECK (
        (enrollment_mode = 'SELF_ENROLLMENT' AND legacy_proof IS NULL)
        OR (enrollment_mode = 'LEGACY_ADOPTION' AND legacy_proof IS NOT NULL)
    ),
    CONSTRAINT ck_dev_enrollment_attempts CHECK (attempt_count >= 0),
    CONSTRAINT ck_dev_enrollment_times CHECK (
        updated_at >= created_at
        AND (completed_at IS NULL OR completed_at >= created_at)
        AND (next_attempt_at IS NULL OR next_attempt_at >= created_at)
    ),
    CONSTRAINT fk_dev_enrollment_challenge
        FOREIGN KEY (challenge_id)
        REFERENCES dev_device_enrollment_challenge (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_enrollment_asset
        FOREIGN KEY (asset_id) REFERENCES dev_device_asset (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_enrollment_work (status, next_attempt_at, id),
    INDEX ix_dev_enrollment_asset (asset_id, id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_device_maintenance_identity (
    asset_id BIGINT NOT NULL,
    enrollment_id BIGINT NOT NULL,
    identity_public_key VARCHAR(128)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    identity_fingerprint_sha256 BINARY(32) NOT NULL,
    tunnel_public_key VARCHAR(128)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tunnel_fingerprint_sha256 BINARY(32) NOT NULL,
    ssh_host_public_key VARCHAR(128)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    ssh_host_fingerprint_sha256 BINARY(32) NOT NULL,
    maintenance_principal VARCHAR(96)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tunnel_server_host VARCHAR(255)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tunnel_server_port INT NOT NULL,
    tunnel_server_user VARCHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tunnel_server_host_public_key VARCHAR(128)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    registered_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (asset_id),
    CONSTRAINT uq_dev_maintenance_identity_enrollment UNIQUE (enrollment_id),
    CONSTRAINT uq_dev_maintenance_identity_key UNIQUE (identity_fingerprint_sha256),
    CONSTRAINT uq_dev_maintenance_tunnel_key UNIQUE (tunnel_fingerprint_sha256),
    CONSTRAINT uq_dev_maintenance_host_key UNIQUE (ssh_host_fingerprint_sha256),
    CONSTRAINT ck_dev_maintenance_principal CHECK (
        maintenance_principal REGEXP '^ecobin-device-[A-Za-z0-9_-]{8,64}$'
    ),
    CONSTRAINT ck_dev_maintenance_tunnel_server CHECK (
        CHAR_LENGTH(TRIM(tunnel_server_host)) > 0
        AND tunnel_server_port BETWEEN 1 AND 65535
        AND tunnel_server_user = 'ecobin-tunnel'
        AND tunnel_server_host_public_key REGEXP
            '^ssh-ed25519 [A-Za-z0-9+/]{68}$'
    ),
    CONSTRAINT ck_dev_maintenance_identity_times CHECK (
        registered_at >= created_at AND updated_at >= created_at
    ),
    CONSTRAINT fk_dev_maintenance_identity_asset
        FOREIGN KEY (asset_id) REFERENCES dev_device_asset (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_maintenance_identity_enrollment
        FOREIGN KEY (enrollment_id) REFERENCES dev_device_enrollment (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE iam_platform_admin_maintenance_ssh_key (
    id BIGINT NOT NULL AUTO_INCREMENT,
    maintenance_ssh_key_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    platform_admin_id BIGINT NOT NULL,
    label VARCHAR(100) NOT NULL,
    public_key VARCHAR(128)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    fingerprint_sha256 VARCHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    revoked_at DATETIME(3) NULL,
    revoked_reason VARCHAR(500) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_iam_platform_maintenance_key_uid
        UNIQUE (maintenance_ssh_key_uid),
    CONSTRAINT uq_iam_platform_maintenance_key_fingerprint
        UNIQUE (platform_admin_id, fingerprint_sha256),
    CONSTRAINT ck_iam_platform_maintenance_key_uid_v4 CHECK (
        maintenance_ssh_key_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_iam_platform_maintenance_key_label CHECK (
        label = TRIM(label) AND CHAR_LENGTH(label) > 0
    ),
    CONSTRAINT ck_iam_platform_maintenance_key_public CHECK (
        public_key REGEXP '^ssh-ed25519 [A-Za-z0-9+/]{68}$'
        AND fingerprint_sha256 REGEXP '^SHA256:[A-Za-z0-9+/]{43}$'
    ),
    CONSTRAINT ck_iam_platform_maintenance_key_revoke CHECK (
        (revoked_at IS NULL AND revoked_reason IS NULL)
        OR (
            revoked_at IS NOT NULL
            AND revoked_reason IS NOT NULL
            AND CHAR_LENGTH(TRIM(revoked_reason)) > 0
        )
    ),
    CONSTRAINT ck_iam_platform_maintenance_key_times CHECK (
        lock_version >= 0
        AND updated_at >= created_at
        AND (revoked_at IS NULL OR (
            revoked_at >= created_at AND updated_at >= revoked_at
        ))
    ),
    CONSTRAINT fk_iam_platform_maintenance_key_admin
        FOREIGN KEY (platform_admin_id) REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_iam_platform_maintenance_key_list (
        platform_admin_id, revoked_at, created_at, id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE iam_factory_operator (
    id BIGINT NOT NULL AUTO_INCREMENT,
    factory_operator_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    operator_code VARCHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    enabled TINYINT NOT NULL,
    auth_version BIGINT NOT NULL DEFAULT 0,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_by_platform_admin_id BIGINT NOT NULL,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_iam_factory_operator_uid UNIQUE (factory_operator_uid),
    CONSTRAINT uq_iam_factory_operator_code UNIQUE (operator_code),
    CONSTRAINT ck_iam_factory_operator_uid_v4 CHECK (
        factory_operator_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_iam_factory_operator_code CHECK (
        operator_code REGEXP '^[A-Z0-9][A-Z0-9_-]{1,63}$'
    ),
    CONSTRAINT ck_iam_factory_operator_name CHECK (
        display_name = TRIM(display_name)
        AND CHAR_LENGTH(display_name) BETWEEN 1 AND 100
    ),
    CONSTRAINT ck_iam_factory_operator_state CHECK (
        enabled IN (0, 1)
        AND auth_version >= 0
        AND lock_version >= 0
        AND updated_at >= created_at
    ),
    CONSTRAINT fk_iam_factory_operator_creator
        FOREIGN KEY (created_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_iam_factory_operator_list (
        enabled, operator_code, id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE iam_factory_operator_binding_intent (
    id BIGINT NOT NULL AUTO_INCREMENT,
    binding_intent_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    factory_operator_id BIGINT NOT NULL,
    miniapp_channel_id BIGINT NOT NULL,
    created_by_platform_admin_id BIGINT NOT NULL,
    binding_token_sha256 BINARY(32) NOT NULL,
    status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    expires_at DATETIME(3) NOT NULL,
    consumed_at DATETIME(3) NULL,
    consumed_wechat_subject_id BIGINT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_iam_factory_operator_intent_uid
        UNIQUE (binding_intent_uid),
    CONSTRAINT uq_iam_factory_operator_intent_token
        UNIQUE (binding_token_sha256),
    CONSTRAINT ck_iam_factory_operator_intent_uid_v4 CHECK (
        binding_intent_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_iam_factory_operator_intent_status CHECK (
        status IN ('PENDING', 'CONSUMED', 'EXPIRED', 'CANCELLED')
        AND (
            (status = 'CONSUMED'
                AND consumed_at IS NOT NULL
                AND consumed_wechat_subject_id IS NOT NULL)
            OR (status <> 'CONSUMED'
                AND consumed_at IS NULL
                AND consumed_wechat_subject_id IS NULL)
        )
    ),
    CONSTRAINT ck_iam_factory_operator_intent_times CHECK (
        expires_at > created_at
        AND (consumed_at IS NULL OR consumed_at >= created_at)
    ),
    CONSTRAINT fk_iam_factory_operator_intent_operator
        FOREIGN KEY (factory_operator_id) REFERENCES iam_factory_operator (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_iam_factory_operator_intent_channel
        FOREIGN KEY (miniapp_channel_id) REFERENCES iam_miniapp_channel (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_iam_factory_operator_intent_creator
        FOREIGN KEY (created_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_iam_factory_operator_intent_subject
        FOREIGN KEY (miniapp_channel_id, consumed_wechat_subject_id)
        REFERENCES iam_wechat_subject (miniapp_channel_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_iam_factory_operator_intent_pending (
        factory_operator_id, status, expires_at, id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE iam_factory_operator_miniapp_binding (
    id BIGINT NOT NULL AUTO_INCREMENT,
    binding_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    factory_operator_id BIGINT NOT NULL,
    miniapp_channel_id BIGINT NOT NULL,
    wechat_subject_id BIGINT NOT NULL,
    status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    bound_at DATETIME(3) NOT NULL,
    revoked_at DATETIME(3) NULL,
    revocation_reason VARCHAR(500) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    active_factory_operator_id BIGINT
        GENERATED ALWAYS AS (
            CASE WHEN status = 'ACTIVE' THEN factory_operator_id ELSE NULL END
        ) STORED,
    active_wechat_subject_id BIGINT
        GENERATED ALWAYS AS (
            CASE WHEN status = 'ACTIVE' THEN wechat_subject_id ELSE NULL END
        ) STORED,
    PRIMARY KEY (id),
    CONSTRAINT uq_iam_factory_operator_binding_uid UNIQUE (binding_uid),
    CONSTRAINT uq_iam_factory_operator_binding_operator
        UNIQUE (active_factory_operator_id),
    CONSTRAINT uq_iam_factory_operator_binding_subject
        UNIQUE (miniapp_channel_id, active_wechat_subject_id),
    CONSTRAINT uq_iam_factory_operator_binding_ref
        UNIQUE (factory_operator_id, miniapp_channel_id, wechat_subject_id, id),
    CONSTRAINT ck_iam_factory_operator_binding_uid_v4 CHECK (
        binding_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_iam_factory_operator_binding_status CHECK (
        status IN ('ACTIVE', 'REVOKED')
        AND (
            (status = 'ACTIVE' AND revoked_at IS NULL
                AND revocation_reason IS NULL)
            OR (status = 'REVOKED' AND revoked_at IS NOT NULL
                AND revocation_reason IS NOT NULL)
        )
    ),
    CONSTRAINT ck_iam_factory_operator_binding_times CHECK (
        lock_version >= 0
        AND bound_at >= created_at
        AND updated_at >= created_at
        AND (revoked_at IS NULL OR revoked_at >= bound_at)
    ),
    CONSTRAINT fk_iam_factory_operator_binding_operator
        FOREIGN KEY (factory_operator_id) REFERENCES iam_factory_operator (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_iam_factory_operator_binding_channel
        FOREIGN KEY (miniapp_channel_id) REFERENCES iam_miniapp_channel (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_iam_factory_operator_binding_subject
        FOREIGN KEY (miniapp_channel_id, wechat_subject_id)
        REFERENCES iam_wechat_subject (miniapp_channel_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_iam_factory_operator_binding_operator (
        factory_operator_id, status, id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE iam_factory_operator_miniapp_session (
    id BIGINT NOT NULL AUTO_INCREMENT,
    session_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    factory_operator_id BIGINT NOT NULL,
    factory_operator_miniapp_binding_id BIGINT NOT NULL,
    miniapp_channel_id BIGINT NOT NULL,
    wechat_subject_id BIGINT NOT NULL,
    issued_at DATETIME(3) NOT NULL,
    expires_at DATETIME(3) NOT NULL,
    revoked_at DATETIME(3) NULL,
    revocation_reason VARCHAR(500) NULL,
    auth_version_snapshot BIGINT NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_iam_factory_operator_session_uid UNIQUE (session_uid),
    CONSTRAINT ck_iam_factory_operator_session_uid_v4 CHECK (
        session_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_iam_factory_operator_session_times CHECK (
        issued_at >= created_at
        AND expires_at > issued_at
        AND (revoked_at IS NULL OR revoked_at >= issued_at)
    ),
    CONSTRAINT ck_iam_factory_operator_session_revoke CHECK (
        (revoked_at IS NULL AND revocation_reason IS NULL)
        OR (revoked_at IS NOT NULL
            AND (revocation_reason IS NULL
                OR CHAR_LENGTH(TRIM(revocation_reason)) > 0))
    ),
    CONSTRAINT ck_iam_factory_operator_session_auth CHECK (
        auth_version_snapshot >= 0
    ),
    CONSTRAINT fk_iam_factory_operator_session_binding
        FOREIGN KEY (
            factory_operator_id,
            miniapp_channel_id,
            wechat_subject_id,
            factory_operator_miniapp_binding_id
        ) REFERENCES iam_factory_operator_miniapp_binding (
            factory_operator_id,
            miniapp_channel_id,
            wechat_subject_id,
            id
        ) ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_iam_factory_operator_session_principal (
        factory_operator_id, revoked_at, expires_at, id
    ),
    INDEX ix_iam_factory_operator_session_expiry (expires_at, id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE ops_audit_log
    ADD COLUMN factory_operator_id BIGINT NULL
        AFTER platform_admin_id,
    DROP CHECK ck_ops_audit_actor,
    DROP CHECK ck_ops_audit_channel,
    ADD CONSTRAINT ck_ops_audit_actor CHECK (
        (
            actor_kind = 'PLATFORM_ADMIN'
            AND platform_admin_id IS NOT NULL
            AND factory_operator_id IS NULL
            AND staff_account_id IS NULL
            AND organization_user_id IS NULL
            AND system_actor_code IS NULL
        )
        OR (
            actor_kind = 'FACTORY_OPERATOR'
            AND scope_kind = 'PLATFORM'
            AND platform_admin_id IS NULL
            AND factory_operator_id IS NOT NULL
            AND staff_account_id IS NULL
            AND organization_user_id IS NULL
            AND system_actor_code IS NULL
        )
        OR (
            actor_kind = 'STAFF_ACCOUNT'
            AND platform_admin_id IS NULL
            AND factory_operator_id IS NULL
            AND staff_account_id IS NOT NULL
            AND organization_user_id IS NULL
            AND system_actor_code IS NULL
        )
        OR (
            actor_kind = 'ORGANIZATION_USER'
            AND scope_kind = 'ORGANIZATION'
            AND platform_admin_id IS NULL
            AND factory_operator_id IS NULL
            AND staff_account_id IS NULL
            AND organization_user_id IS NOT NULL
            AND system_actor_code IS NULL
        )
        OR (
            actor_kind = 'SYSTEM'
            AND platform_admin_id IS NULL
            AND factory_operator_id IS NULL
            AND staff_account_id IS NULL
            AND organization_user_id IS NULL
            AND system_actor_code IS NOT NULL
            AND system_actor_code = UPPER(TRIM(system_actor_code))
        )
        OR (
            actor_kind = 'UNAUTHENTICATED'
            AND platform_admin_id IS NULL
            AND factory_operator_id IS NULL
            AND staff_account_id IS NULL
            AND organization_user_id IS NULL
            AND system_actor_code IS NULL
        )
    ),
    ADD CONSTRAINT ck_ops_audit_channel CHECK (
        entry_channel IN (
            'WEB',
            'MINIAPP_MANAGEMENT',
            'MINIAPP_FACTORY',
            'MINIAPP_USER',
            'SYSTEM_TASK',
            'SECURITY_ENTRY'
        )
    ),
    ADD CONSTRAINT fk_ops_audit_factory_operator
        FOREIGN KEY (factory_operator_id)
        REFERENCES iam_factory_operator (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_ops_audit_factory_operator_fk (
        factory_operator_id, occurred_at, id
    );

ALTER TABLE dev_factory_installed_bag
    ADD COLUMN installation_source VARCHAR(24)
        CHARACTER SET ascii COLLATE ascii_bin
        NOT NULL DEFAULT 'PLATFORM_CREATE'
        AFTER bag_code,
    ADD COLUMN installed_by_factory_operator_id BIGINT NULL
        AFTER installation_source,
    ADD COLUMN label_item_id BIGINT NULL
        AFTER installed_by_factory_operator_id,
    ADD CONSTRAINT ck_dev_factory_bag_source_v52 CHECK (
        (
            installation_source IN (
                'PLATFORM_CREATE', 'LEGACY_GRANDFATHERED'
            )
            AND installed_by_factory_operator_id IS NULL
            AND label_item_id IS NULL
        )
        OR (
            installation_source = 'FACTORY_MINIAPP'
            AND installed_by_factory_operator_id IS NOT NULL
            AND label_item_id IS NOT NULL
        )
    ),
    ADD CONSTRAINT fk_dev_factory_bag_operator_v52
        FOREIGN KEY (installed_by_factory_operator_id)
        REFERENCES iam_factory_operator (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_dev_factory_bag_label_v52
        FOREIGN KEY (label_item_id) REFERENCES rec_bag_label_item (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT uq_dev_factory_bag_label_v52 UNIQUE (label_item_id);

-- Existing accepted or already assigned assets keep their historical bag
-- facts. Mutable factory assets must obtain a real miniapp scan before a
-- challenge can be scheduled.
UPDATE dev_factory_installed_bag bag
JOIN dev_device_asset asset ON asset.id = bag.asset_id
SET bag.installation_source = 'LEGACY_GRANDFATHERED'
WHERE asset.acceptance_status = 'PASSED'
   OR asset.tenant_id IS NOT NULL;

UPDATE dev_device_asset asset
JOIN (
    SELECT bag.asset_id,
           UNHEX(SHA2(GROUP_CONCAT(
               CONCAT(bag.port_no, ':', bag.bag_code)
               ORDER BY bag.port_no SEPARATOR '\n'
           ), 256)) AS bag_set_sha256
    FROM dev_factory_installed_bag bag
    GROUP BY bag.asset_id
) current_bags ON current_bags.asset_id = asset.id
SET asset.factory_bag_set_sha256 = current_bags.bag_set_sha256;

ALTER TABLE dev_device_acceptance_evidence
    ADD COLUMN factory_bag_revision BIGINT UNSIGNED NULL
        AFTER command_uid,
    ADD COLUMN factory_bag_set_sha256 BINARY(32) NULL
        AFTER factory_bag_revision,
    ADD CONSTRAINT ck_dev_acceptance_factory_bag_snapshot_v52 CHECK (
        (
            evidence_schema_version < 3
            AND factory_bag_revision IS NULL
            AND factory_bag_set_sha256 IS NULL
        )
        OR (
            evidence_schema_version >= 3
            AND factory_bag_revision IS NOT NULL
            AND factory_bag_set_sha256 IS NOT NULL
        )
    );

CREATE TABLE rec_bag_label_claim (
    id BIGINT NOT NULL AUTO_INCREMENT,
    claim_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    label_item_id BIGINT NOT NULL,
    claim_kind VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    asset_id BIGINT NOT NULL,
    port_no SMALLINT NOT NULL,
    claimed_by_factory_operator_id BIGINT NOT NULL,
    claimed_at DATETIME(3) NOT NULL,
    released_at DATETIME(3) NULL,
    release_reason VARCHAR(500) NULL,
    active_label_item_id BIGINT
        GENERATED ALWAYS AS (
            CASE WHEN released_at IS NULL THEN label_item_id ELSE NULL END
        ) STORED,
    active_asset_id BIGINT
        GENERATED ALWAYS AS (
            CASE WHEN released_at IS NULL THEN asset_id ELSE NULL END
        ) STORED,
    active_port_no SMALLINT
        GENERATED ALWAYS AS (
            CASE WHEN released_at IS NULL THEN port_no ELSE NULL END
        ) STORED,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_rec_bag_label_claim_uid UNIQUE (claim_uid),
    CONSTRAINT uq_rec_bag_label_claim_active_label
        UNIQUE (active_label_item_id),
    CONSTRAINT uq_rec_bag_label_claim_active_port
        UNIQUE (active_asset_id, active_port_no),
    CONSTRAINT ck_rec_bag_label_claim_uid_v4 CHECK (
        claim_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_rec_bag_label_claim_kind CHECK (
        claim_kind = 'FACTORY_INSTALLATION'
        AND port_no BETWEEN 1 AND 6
    ),
    CONSTRAINT ck_rec_bag_label_claim_release CHECK (
        (released_at IS NULL AND release_reason IS NULL)
        OR (released_at IS NOT NULL
            AND release_reason IS NOT NULL
            AND CHAR_LENGTH(TRIM(release_reason)) > 0)
    ),
    CONSTRAINT ck_rec_bag_label_claim_times CHECK (
        claimed_at >= created_at
        AND (released_at IS NULL OR released_at >= claimed_at)
    ),
    CONSTRAINT fk_rec_bag_label_claim_item
        FOREIGN KEY (label_item_id) REFERENCES rec_bag_label_item (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_bag_label_claim_asset
        FOREIGN KEY (asset_id) REFERENCES dev_device_asset (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_bag_label_claim_operator
        FOREIGN KEY (claimed_by_factory_operator_id)
        REFERENCES iam_factory_operator (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_bag_label_claim_asset_history (
        asset_id, port_no, claimed_at DESC, id DESC
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_factory_installed_bag_change (
    id BIGINT NOT NULL AUTO_INCREMENT,
    change_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    request_sha256 BINARY(32) NOT NULL,
    asset_id BIGINT NOT NULL,
    port_no SMALLINT NOT NULL,
    change_kind VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    previous_bag_code VARCHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    current_bag_code VARCHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    factory_operator_id BIGINT NOT NULL,
    reason VARCHAR(500) NULL,
    occurred_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_factory_bag_change_uid UNIQUE (change_uid),
    CONSTRAINT ck_dev_factory_bag_change_uid_v4 CHECK (
        change_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_factory_bag_change_shape CHECK (
        port_no BETWEEN 1 AND 6
        AND (
            (change_kind = 'INSTALLED'
                AND previous_bag_code IS NULL
                AND reason IS NULL)
            OR (change_kind = 'VERIFIED'
                AND previous_bag_code = current_bag_code
                AND reason IS NULL)
            OR (change_kind = 'CORRECTED'
                AND previous_bag_code IS NOT NULL
                AND reason IS NOT NULL
                AND CHAR_LENGTH(TRIM(reason)) > 0)
        )
    ),
    CONSTRAINT ck_dev_factory_bag_change_times CHECK (
        occurred_at >= created_at
    ),
    CONSTRAINT fk_dev_factory_bag_change_asset
        FOREIGN KEY (asset_id) REFERENCES dev_device_asset (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_factory_bag_change_operator
        FOREIGN KEY (factory_operator_id) REFERENCES iam_factory_operator (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_factory_bag_change_history (
        asset_id, port_no, occurred_at DESC, id DESC
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_remote_support_port_slot (
    port_no INT NOT NULL,
    enabled TINYINT NOT NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (port_no),
    CONSTRAINT ck_dev_remote_support_port_slot CHECK (
        port_no BETWEEN 22011 AND 22014
        AND enabled IN (0, 1)
        AND lock_version >= 0
        AND updated_at >= created_at
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO dev_remote_support_port_slot (
    port_no, enabled, lock_version, created_at, updated_at
) VALUES
    (22011, 1, 0, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)),
    (22012, 1, 0, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)),
    (22013, 1, 0, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)),
    (22014, 1, 0, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3));

CREATE TABLE dev_remote_support_session (
    id BIGINT NOT NULL AUTO_INCREMENT,
    session_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    operation_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    request_sha256 BINARY(32) NOT NULL,
    asset_id BIGINT NOT NULL,
    requested_by_platform_admin_id BIGINT NOT NULL,
    requested_by_platform_admin_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    maintenance_ssh_key_id BIGINT NOT NULL,
    maintenance_ssh_key_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tunnel_public_key VARCHAR(128)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tunnel_fingerprint_sha256 BINARY(32) NOT NULL,
    ssh_host_public_key VARCHAR(128)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    reason VARCHAR(500) NOT NULL,
    state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    port_no INT NOT NULL,
    open_command_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    close_operation_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    close_request_sha256 BINARY(32) NULL,
    close_command_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    device_reported_state VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    server_lease_state VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    failure_code VARCHAR(100)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    failure_detail VARCHAR(500) NULL,
    certificate_serial BIGINT UNSIGNED NULL,
    certificate_text VARCHAR(2048)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    certificate_sha256 BINARY(32) NULL,
    certificate_issued_at DATETIME(3) NULL,
    connect_deadline_at DATETIME(3) NOT NULL,
    expires_at DATETIME(3) NOT NULL,
    opened_at DATETIME(3) NULL,
    close_requested_at DATETIME(3) NULL,
    closed_at DATETIME(3) NULL,
    lease_released_at DATETIME(3) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    active_asset_id BIGINT
        GENERATED ALWAYS AS (
            CASE WHEN lease_released_at IS NULL
                THEN asset_id ELSE NULL END
        ) STORED,
    active_port_no INT
        GENERATED ALWAYS AS (
            CASE WHEN lease_released_at IS NULL
                THEN port_no ELSE NULL END
        ) STORED,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_remote_support_session_uid UNIQUE (session_uid),
    CONSTRAINT uq_dev_remote_support_operation UNIQUE (operation_uid),
    CONSTRAINT uq_dev_remote_support_close_operation
        UNIQUE (close_operation_uid),
    CONSTRAINT uq_dev_remote_support_active_asset UNIQUE (active_asset_id),
    CONSTRAINT uq_dev_remote_support_active_port UNIQUE (active_port_no),
    CONSTRAINT uq_dev_remote_support_certificate_serial
        UNIQUE (certificate_serial),
    CONSTRAINT ck_dev_remote_support_uids_v4 CHECK (
        session_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND operation_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND requested_by_platform_admin_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND maintenance_ssh_key_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND open_command_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND (close_operation_uid IS NULL OR close_operation_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
        AND (close_command_uid IS NULL OR close_command_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
    ),
    CONSTRAINT ck_dev_remote_support_close_request CHECK (
        (close_operation_uid IS NULL
            AND close_request_sha256 IS NULL
            AND close_command_uid IS NULL
            AND close_requested_at IS NULL)
        OR (close_operation_uid IS NOT NULL
            AND close_request_sha256 IS NOT NULL
            AND close_command_uid IS NOT NULL
            AND close_requested_at IS NOT NULL)
    ),
    CONSTRAINT ck_dev_remote_support_reason CHECK (
        reason = TRIM(reason) AND CHAR_LENGTH(reason) > 0
    ),
    CONSTRAINT ck_dev_remote_support_identity_snapshot CHECK (
        tunnel_public_key REGEXP '^ssh-ed25519 [A-Za-z0-9+/]{68}$'
        AND ssh_host_public_key REGEXP
            '^ssh-ed25519 [A-Za-z0-9+/]{68}$'
    ),
    CONSTRAINT ck_dev_remote_support_state CHECK (
        state IN (
            'PREPARING', 'CONNECTING', 'OPEN', 'RECONNECTING', 'CLOSING',
            'CLOSED', 'FAILED', 'EXPIRED'
        )
        AND (device_reported_state IS NULL OR device_reported_state IN (
            'CONNECTING', 'OPEN', 'CLOSED', 'FAILED', 'EXPIRED'
        ))
        AND server_lease_state IN (
            'DESIRED', 'ACTIVE', 'REVOKED', 'ABSENT', 'ERROR'
        )
    ),
    CONSTRAINT ck_dev_remote_support_certificate CHECK (
        (
            certificate_serial IS NULL
            AND certificate_text IS NULL
            AND certificate_sha256 IS NULL
            AND certificate_issued_at IS NULL
        )
        OR (
            certificate_serial IS NOT NULL
            AND certificate_text IS NOT NULL
            AND certificate_sha256 IS NOT NULL
            AND certificate_issued_at IS NOT NULL
        )
    ),
    CONSTRAINT ck_dev_remote_support_failure CHECK (
        (state = 'FAILED' AND failure_code IS NOT NULL)
        OR (state <> 'FAILED' AND failure_code IS NULL
            AND failure_detail IS NULL)
    ),
    CONSTRAINT ck_dev_remote_support_times CHECK (
        lock_version >= 0
        AND connect_deadline_at > created_at
        AND expires_at > connect_deadline_at
        AND updated_at >= created_at
        AND (opened_at IS NULL OR opened_at >= created_at)
        AND (close_requested_at IS NULL
            OR close_requested_at >= created_at)
        AND (closed_at IS NULL OR closed_at >= created_at)
        AND (lease_released_at IS NULL
            OR lease_released_at >= created_at)
        AND (certificate_issued_at IS NULL
            OR certificate_issued_at >= created_at)
    ),
    CONSTRAINT fk_dev_remote_support_asset
        FOREIGN KEY (asset_id) REFERENCES dev_device_asset (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_remote_support_admin
        FOREIGN KEY (requested_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_remote_support_ssh_key
        FOREIGN KEY (maintenance_ssh_key_id)
        REFERENCES iam_platform_admin_maintenance_ssh_key (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_remote_support_port
        FOREIGN KEY (port_no) REFERENCES dev_remote_support_port_slot (port_no)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_remote_support_expiry (state, expires_at, id),
    INDEX ix_dev_remote_support_admin (
        requested_by_platform_admin_id, created_at DESC, id DESC
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_remote_support_status_event (
    id BIGINT NOT NULL AUTO_INCREMENT,
    event_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    session_id BIGINT NOT NULL,
    source_inbox_id BIGINT NOT NULL,
    reported_state VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    failure_code VARCHAR(100)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    ssh_exit_code INT NULL,
    event_sha256 BINARY(32) NOT NULL,
    occurred_at DATETIME(3) NOT NULL,
    received_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_remote_support_status_event_uid UNIQUE (event_uid),
    CONSTRAINT uq_dev_remote_support_status_inbox UNIQUE (source_inbox_id),
    CONSTRAINT uq_dev_remote_support_status_digest
        UNIQUE (session_id, event_sha256),
    CONSTRAINT ck_dev_remote_support_status_event_uid_v4 CHECK (
        event_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_remote_support_status_event_state CHECK (
        reported_state IN (
            'CONNECTING', 'OPEN', 'CLOSED', 'FAILED', 'EXPIRED'
        )
        AND (reported_state <> 'FAILED' OR failure_code IS NOT NULL)
        AND (failure_code IS NULL OR failure_code REGEXP
            '^[A-Z][A-Z0-9_]{0,63}$')
    ),
    CONSTRAINT ck_dev_remote_support_status_event_times CHECK (
        received_at >= occurred_at AND created_at = received_at
    ),
    CONSTRAINT fk_dev_remote_support_status_session
        FOREIGN KEY (session_id) REFERENCES dev_remote_support_session (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_remote_support_status_inbox
        FOREIGN KEY (source_inbox_id) REFERENCES ops_inbox_message (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_remote_support_status_history (
        session_id, occurred_at DESC, id DESC
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Remote-support status is a reliable platform-scoped device fact.
ALTER TABLE dev_edge_event
    DROP CHECK ck_dev_edge_event_type_class,
    DROP CHECK ck_dev_edge_event_target_pair,
    ADD CONSTRAINT ck_dev_edge_event_type_class_v52 CHECK (
        (
            event_type IN (
                'DEVICE_COMMAND_OBSERVED',
                'CONFIGURATION_PROGRESS',
                'DELIVERY_COMPLETE',
                'CLEAN_COMPLETE',
                'FULLNESS_SAMPLE_COMPLETE',
                'FULLNESS_STATE_CHANGED',
                'BASELINE_MEASUREMENT_COMPLETE',
                'DEVICE_FAULT_OBSERVED',
                'DEVICE_FAULT_RECOVERED',
                'SAFETY_SENSOR_STATE_CHANGED',
                'PHOTO_STATUS_REPORTED',
                'PHOTO_UPLOAD_GRANT_REQUESTED',
                'REMOTE_SUPPORT_TUNNEL_STATUS'
            )
            AND delivery_class = 'RELIABLE_FACT'
        )
        OR (
            event_type = 'BUSINESS_CONFIRMATION_RECEIPT'
            AND delivery_class = 'CONTROL_RECEIPT'
        )
        OR (
            event_type = 'DEVICE_RUNTIME_SNAPSHOT'
            AND delivery_class = 'TELEMETRY_SNAPSHOT'
        )
    ),
    ADD CONSTRAINT ck_dev_edge_event_target_pair_v52 CHECK (
        (event_type = 'DEVICE_COMMAND_OBSERVED'
            AND target_type = 'DEVICE_COMMAND')
        OR (event_type = 'CONFIGURATION_PROGRESS'
            AND target_type = 'CONFIGURATION_APPLICATION')
        OR (event_type = 'DELIVERY_COMPLETE'
            AND target_type = 'DELIVERY_SESSION')
        OR (event_type = 'CLEAN_COMPLETE'
            AND target_type = 'CLEAN_OPERATION')
        OR (event_type = 'FULLNESS_SAMPLE_COMPLETE'
            AND target_type = 'FULLNESS_DETECTION')
        OR (event_type = 'FULLNESS_STATE_CHANGED'
            AND target_type = 'PORT_FULLNESS_STATE')
        OR (event_type = 'BASELINE_MEASUREMENT_COMPLETE'
            AND target_type = 'BASELINE_MEASUREMENT')
        OR (
            event_type IN (
                'DEVICE_FAULT_OBSERVED',
                'DEVICE_FAULT_RECOVERED',
                'SAFETY_SENSOR_STATE_CHANGED',
                'DEVICE_RUNTIME_SNAPSHOT',
                'REMOTE_SUPPORT_TUNNEL_STATUS'
            )
            AND target_type = 'DEVICE_ASSET'
        )
        OR (
            event_type IN (
                'PHOTO_STATUS_REPORTED',
                'PHOTO_UPLOAD_GRANT_REQUESTED'
            )
            AND target_type IN ('DELIVERY_SESSION', 'CLEAN_OPERATION')
        )
        OR (event_type = 'BUSINESS_CONFIRMATION_RECEIPT'
            AND target_type = 'BUSINESS_CONFIRMATION')
    );

ALTER TABLE ops_reliable_task
    DROP CHECK ck_ops_task_sources_v42,
    ADD CONSTRAINT ck_ops_task_sources_v52 CHECK (
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
                                'SYNC_DEVICE_ENTRY_URL',
                                'OPEN_REMOTE_SUPPORT_TUNNEL',
                                'CLOSE_REMOTE_SUPPORT_TUNNEL',
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
