-- EcoBin P0 reliable execution and operations governance.
-- V7 creates exactly 9 tables. Domain facts remain authoritative; these
-- tables record trusted receipt, execution, audit, alert, and reconciliation.

CREATE TABLE ops_inbox_message (
    id BIGINT NOT NULL AUTO_INCREMENT,
    inbox_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    scope_kind VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NULL,
    organization_id BIGINT NULL,
    source_namespace VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_principal_key VARCHAR(160) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    external_message_id VARCHAR(160) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    message_kind VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    normalized_schema_version INT NOT NULL,
    raw_transport_body MEDIUMBLOB NOT NULL,
    raw_transport_sha256 BINARY(32) NOT NULL,
    normalized_payload JSON NOT NULL,
    normalized_content_sha256 BINARY(32) NOT NULL,
    authentication_method VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin
        NOT NULL,
    authentication_principal_ref VARCHAR(255) CHARACTER SET ascii
        COLLATE ascii_bin NOT NULL,
    correlation_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    causation_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    processing_state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    first_received_at DATETIME(3) NOT NULL,
    last_received_at DATETIME(3) NOT NULL,
    delivery_count BIGINT NOT NULL DEFAULT 1,
    processed_at DATETIME(3) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_ops_inbox_uid UNIQUE (inbox_uid),
    CONSTRAINT uq_ops_inbox_external_identity UNIQUE (
        source_namespace,
        source_principal_key,
        external_message_id
    ),
    CONSTRAINT uq_ops_inbox_scope_kind_id UNIQUE (scope_kind, id),
    CONSTRAINT uq_ops_inbox_scope_tenant_id
        UNIQUE (scope_kind, tenant_id, id),
    CONSTRAINT uq_ops_inbox_scope_org_id
        UNIQUE (scope_kind, tenant_id, organization_id, id),
    CONSTRAINT ck_ops_inbox_uid_v4 CHECK (
        inbox_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_ops_inbox_scope CHECK (
        (
            scope_kind = 'PLATFORM'
            AND tenant_id IS NULL
            AND organization_id IS NULL
        )
        OR
        (
            scope_kind = 'TENANT'
            AND tenant_id IS NOT NULL
            AND organization_id IS NULL
        )
        OR
        (
            scope_kind = 'ORGANIZATION'
            AND tenant_id IS NOT NULL
            AND organization_id IS NOT NULL
        )
    ),
    CONSTRAINT ck_ops_inbox_source CHECK (
        source_namespace = LOWER(TRIM(source_namespace))
        AND CHAR_LENGTH(source_namespace) > 0
        AND CHAR_LENGTH(TRIM(source_principal_key)) > 0
        AND CHAR_LENGTH(TRIM(external_message_id)) > 0
        AND CHAR_LENGTH(TRIM(message_kind)) > 0
        AND normalized_schema_version BETWEEN 1 AND 2147483647
    ),
    CONSTRAINT ck_ops_inbox_payload_bounds CHECK (
        OCTET_LENGTH(raw_transport_body) BETWEEN 1 AND 1048576
        AND OCTET_LENGTH(CAST(normalized_payload AS CHAR)) <= 262144
        AND JSON_TYPE(normalized_payload) = 'OBJECT'
    ),
    CONSTRAINT ck_ops_inbox_state CHECK (
        (
            processing_state = 'RECEIVED'
            AND processed_at IS NULL
        )
        OR
        (
            processing_state = 'PROCESSED'
            AND processed_at IS NOT NULL
        )
    ),
    CONSTRAINT ck_ops_inbox_values CHECK (
        delivery_count BETWEEN 1 AND 9007199254740991
        AND lock_version >= 0
    ),
    CONSTRAINT ck_ops_inbox_times CHECK (
        first_received_at >= created_at
        AND last_received_at >= first_received_at
        AND (processed_at IS NULL OR processed_at >= first_received_at)
        AND updated_at >= created_at
    ),
    CONSTRAINT fk_ops_inbox_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES iam_tenant (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_inbox_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_ops_inbox_tenant_fk (tenant_id),
    INDEX ix_ops_inbox_org_fk (tenant_id, organization_id),
    INDEX ix_ops_inbox_pending (
        processing_state,
        first_received_at,
        id
    ),
    INDEX ix_ops_inbox_scope_time (
        scope_kind,
        tenant_id,
        organization_id,
        first_received_at,
        id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE ops_audit_log (
    id BIGINT NOT NULL AUTO_INCREMENT,
    audit_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    request_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    operation_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    scope_kind VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NULL,
    organization_id BIGINT NULL,
    actor_kind VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    platform_admin_id BIGINT NULL,
    staff_account_id BIGINT NULL,
    system_actor_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    actor_display_snapshot VARCHAR(100) NULL,
    action_code VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    target_type VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    target_stable_key VARCHAR(160) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    entry_channel VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    result VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    session_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    correlation_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    causation_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    remote_ip VARBINARY(16) NULL,
    user_agent_sha256 BINARY(32) NULL,
    reason VARCHAR(500) NULL,
    safe_change_summary JSON NULL,
    occurred_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    succeeded_operation_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin
        GENERATED ALWAYS AS (
            CASE WHEN result = 'SUCCEEDED' THEN operation_uid ELSE NULL END
        ) STORED,
    PRIMARY KEY (id),
    CONSTRAINT uq_ops_audit_uid UNIQUE (audit_uid),
    CONSTRAINT uq_ops_audit_request UNIQUE (request_uid),
    CONSTRAINT uq_ops_audit_succeeded_operation UNIQUE (succeeded_operation_uid),
    CONSTRAINT uq_ops_audit_scope_kind_id UNIQUE (scope_kind, id),
    CONSTRAINT uq_ops_audit_scope_tenant_id
        UNIQUE (scope_kind, tenant_id, id),
    CONSTRAINT uq_ops_audit_scope_org_id
        UNIQUE (scope_kind, tenant_id, organization_id, id),
    CONSTRAINT ck_ops_audit_uids_v4 CHECK (
        audit_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND request_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND (
            operation_uid IS NULL
            OR operation_uid REGEXP
                '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        )
    ),
    CONSTRAINT ck_ops_audit_scope CHECK (
        (
            scope_kind = 'PLATFORM'
            AND tenant_id IS NULL
            AND organization_id IS NULL
        )
        OR
        (
            scope_kind = 'TENANT'
            AND tenant_id IS NOT NULL
            AND organization_id IS NULL
        )
        OR
        (
            scope_kind = 'ORGANIZATION'
            AND tenant_id IS NOT NULL
            AND organization_id IS NOT NULL
        )
        OR
        (
            scope_kind = 'UNRESOLVED'
            AND tenant_id IS NULL
            AND organization_id IS NULL
        )
    ),
    CONSTRAINT ck_ops_audit_actor CHECK (
        (
            actor_kind = 'PLATFORM_ADMIN'
            AND platform_admin_id IS NOT NULL
            AND staff_account_id IS NULL
            AND system_actor_code IS NULL
        )
        OR
        (
            actor_kind = 'STAFF_ACCOUNT'
            AND platform_admin_id IS NULL
            AND staff_account_id IS NOT NULL
            AND system_actor_code IS NULL
        )
        OR
        (
            actor_kind = 'SYSTEM'
            AND platform_admin_id IS NULL
            AND staff_account_id IS NULL
            AND system_actor_code IS NOT NULL
            AND system_actor_code = UPPER(TRIM(system_actor_code))
        )
        OR
        (
            actor_kind = 'UNAUTHENTICATED'
            AND platform_admin_id IS NULL
            AND staff_account_id IS NULL
            AND system_actor_code IS NULL
        )
    ),
    CONSTRAINT ck_ops_audit_action CHECK (
        action_code = LOWER(TRIM(action_code))
        AND CHAR_LENGTH(action_code) > 0
        AND CHAR_LENGTH(TRIM(target_type)) > 0
        AND CHAR_LENGTH(TRIM(target_stable_key)) > 0
    ),
    CONSTRAINT ck_ops_audit_channel CHECK (
        entry_channel IN (
            'WEB',
            'MINIAPP_MANAGEMENT',
            'SYSTEM_TASK',
            'SECURITY_ENTRY'
        )
    ),
    CONSTRAINT ck_ops_audit_result CHECK (
        result IN ('SUCCEEDED', 'DENIED', 'FAILED')
        AND (result <> 'SUCCEEDED' OR operation_uid IS NOT NULL)
    ),
    CONSTRAINT ck_ops_audit_safe_summary CHECK (
        safe_change_summary IS NULL
        OR JSON_TYPE(safe_change_summary) = 'OBJECT'
    ),
    CONSTRAINT ck_ops_audit_times CHECK (occurred_at >= created_at),
    CONSTRAINT fk_ops_audit_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES iam_tenant (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_audit_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_audit_platform_actor
        FOREIGN KEY (platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_audit_staff_actor
        FOREIGN KEY (tenant_id, staff_account_id)
        REFERENCES iam_staff_account (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_ops_audit_tenant_fk (tenant_id),
    INDEX ix_ops_audit_org_fk (tenant_id, organization_id),
    INDEX ix_ops_audit_platform_actor_fk (platform_admin_id),
    INDEX ix_ops_audit_staff_actor_fk (tenant_id, staff_account_id),
    INDEX ix_ops_audit_scope_time (
        scope_kind,
        tenant_id,
        organization_id,
        occurred_at,
        id
    ),
    INDEX ix_ops_audit_action_result (
        action_code,
        result,
        occurred_at,
        id
    ),
    INDEX ix_ops_audit_actor_time (
        actor_kind,
        staff_account_id,
        platform_admin_id,
        occurred_at,
        id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE ops_message_quarantine (
    id BIGINT NOT NULL AUTO_INCREMENT,
    quarantine_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    dedupe_key BINARY(32) NOT NULL,
    scope_kind VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NULL,
    organization_id BIGINT NULL,
    source_namespace VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_principal_key VARCHAR(160) CHARACTER SET ascii COLLATE ascii_bin NULL,
    external_message_id VARCHAR(160) CHARACTER SET ascii COLLATE ascii_bin NULL,
    conflicting_inbox_id BIGINT NULL,
    reason_code VARCHAR(40) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    raw_transport_sha256 BINARY(32) NOT NULL,
    normalized_content_sha256 BINARY(32) NULL,
    redacted_diagnostic_payload VARCHAR(2000) NULL,
    status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    first_seen_at DATETIME(3) NOT NULL,
    last_seen_at DATETIME(3) NOT NULL,
    discovery_count BIGINT NOT NULL DEFAULT 1,
    acknowledged_audit_id BIGINT NULL,
    acknowledged_at DATETIME(3) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_ops_quarantine_uid UNIQUE (quarantine_uid),
    CONSTRAINT uq_ops_quarantine_dedupe UNIQUE (dedupe_key),
    CONSTRAINT uq_ops_quarantine_ack_audit UNIQUE (acknowledged_audit_id),
    CONSTRAINT uq_ops_quarantine_scope_kind_id UNIQUE (scope_kind, id),
    CONSTRAINT uq_ops_quarantine_scope_tenant_id
        UNIQUE (scope_kind, tenant_id, id),
    CONSTRAINT uq_ops_quarantine_scope_org_id
        UNIQUE (scope_kind, tenant_id, organization_id, id),
    CONSTRAINT ck_ops_quarantine_uid_v4 CHECK (
        quarantine_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_ops_quarantine_scope CHECK (
        (
            scope_kind IN ('PLATFORM', 'UNRESOLVED')
            AND tenant_id IS NULL
            AND organization_id IS NULL
        )
        OR
        (
            scope_kind = 'TENANT'
            AND tenant_id IS NOT NULL
            AND organization_id IS NULL
        )
        OR
        (
            scope_kind = 'ORGANIZATION'
            AND tenant_id IS NOT NULL
            AND organization_id IS NOT NULL
        )
    ),
    CONSTRAINT ck_ops_quarantine_reason CHECK (
        reason_code IN (
            'MISSING_STABLE_ID',
            'PERMANENT_FORMAT_ERROR',
            'UNSUPPORTED_SCHEMA',
            'UNRESOLVED_SCOPE',
            'IDENTITY_CONTENT_CONFLICT',
            'SCOPE_CONFLICT'
        )
    ),
    CONSTRAINT ck_ops_quarantine_status CHECK (
        (
            status = 'OPEN'
            AND acknowledged_audit_id IS NULL
            AND acknowledged_at IS NULL
        )
        OR
        (
            status = 'ACKNOWLEDGED'
            AND acknowledged_audit_id IS NOT NULL
            AND acknowledged_at IS NOT NULL
        )
    ),
    CONSTRAINT ck_ops_quarantine_values CHECK (
        discovery_count BETWEEN 1 AND 9007199254740991
        AND lock_version >= 0
        AND (
            redacted_diagnostic_payload IS NULL
            OR CHAR_LENGTH(redacted_diagnostic_payload) <= 2000
        )
    ),
    CONSTRAINT ck_ops_quarantine_times CHECK (
        first_seen_at >= created_at
        AND last_seen_at >= first_seen_at
        AND (
            acknowledged_at IS NULL
            OR acknowledged_at >= first_seen_at
        )
        AND updated_at >= created_at
    ),
    CONSTRAINT fk_ops_quarantine_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES iam_tenant (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_quarantine_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_quarantine_inbox
        FOREIGN KEY (conflicting_inbox_id)
        REFERENCES ops_inbox_message (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_quarantine_ack_audit
        FOREIGN KEY (acknowledged_audit_id)
        REFERENCES ops_audit_log (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_ops_quarantine_tenant_fk (tenant_id),
    INDEX ix_ops_quarantine_org_fk (tenant_id, organization_id),
    INDEX ix_ops_quarantine_inbox_fk (conflicting_inbox_id),
    INDEX ix_ops_quarantine_ack_audit_fk (acknowledged_audit_id),
    INDEX ix_ops_quarantine_queue (
        status,
        reason_code,
        first_seen_at,
        id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE ops_reliable_task (
    id BIGINT NOT NULL AUTO_INCREMENT,
    task_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    scope_kind VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NULL,
    organization_id BIGINT NULL,
    task_category VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    task_type VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    execution_lane VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    task_key VARCHAR(255) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    target_type VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    target_stable_key VARCHAR(160) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_inbox_id BIGINT NULL,
    source_device_deployment_id BIGINT NULL,
    source_device_command_id BIGINT NULL,
    payload_schema_version INT NOT NULL,
    redacted_execution_snapshot JSON NOT NULL,
    payload_sha256 BINARY(32) NOT NULL,
    correlation_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    causation_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    initiating_audit_id BIGINT NULL,
    priority SMALLINT NOT NULL DEFAULT 100,
    retry_policy_version INT NOT NULL,
    max_auto_attempts INT NOT NULL,
    state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    next_run_at DATETIME(3) NULL,
    lease_token CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    lease_worker VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NULL,
    lease_until DATETIME(3) NULL,
    attempt_sequence BIGINT NOT NULL DEFAULT 0,
    consecutive_failure_count INT NOT NULL DEFAULT 0,
    wake_version BIGINT NOT NULL DEFAULT 0,
    handled_wake_version BIGINT NOT NULL DEFAULT 0,
    completed_at DATETIME(3) NULL,
    blocked_reason_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    blocked_diagnostic VARCHAR(500) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    claimable_at DATETIME(3)
        GENERATED ALWAYS AS (COALESCE(lease_until, next_run_at)) STORED,
    PRIMARY KEY (id),
    CONSTRAINT uq_ops_task_uid UNIQUE (task_uid),
    CONSTRAINT uq_ops_task_key UNIQUE (task_key),
    CONSTRAINT uq_ops_task_inbox UNIQUE (source_inbox_id),
    CONSTRAINT uq_ops_task_device_command UNIQUE (source_device_command_id),
    CONSTRAINT uq_ops_task_scope_kind_id UNIQUE (scope_kind, id),
    CONSTRAINT uq_ops_task_scope_tenant_id
        UNIQUE (scope_kind, tenant_id, id),
    CONSTRAINT uq_ops_task_scope_org_id
        UNIQUE (scope_kind, tenant_id, organization_id, id),
    CONSTRAINT ck_ops_task_uid_v4 CHECK (
        task_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_ops_task_scope CHECK (
        (
            scope_kind = 'PLATFORM'
            AND tenant_id IS NULL
            AND organization_id IS NULL
        )
        OR
        (
            scope_kind = 'TENANT'
            AND tenant_id IS NOT NULL
            AND organization_id IS NULL
        )
        OR
        (
            scope_kind = 'ORGANIZATION'
            AND tenant_id IS NOT NULL
            AND organization_id IS NOT NULL
        )
    ),
    CONSTRAINT ck_ops_task_category CHECK (
        task_category IN (
            'BUSINESS_INTENT',
            'INBOX_PROCESSING',
            'TIMER',
            'RECONCILIATION'
        )
        AND execution_lane IN ('DEVICE', 'FUNDS')
    ),
    CONSTRAINT ck_ops_task_key CHECK (
        task_key REGEXP '^[A-Z0-9_:-]{8,255}$'
        AND CHAR_LENGTH(TRIM(task_type)) > 0
        AND CHAR_LENGTH(TRIM(target_type)) > 0
        AND CHAR_LENGTH(TRIM(target_stable_key)) > 0
    ),
    CONSTRAINT ck_ops_task_sources CHECK (
        (
            task_category = 'INBOX_PROCESSING'
            AND source_inbox_id IS NOT NULL
            AND source_device_deployment_id IS NULL
            AND source_device_command_id IS NULL
        )
        OR
        (
            task_category <> 'INBOX_PROCESSING'
            AND source_inbox_id IS NULL
            AND (
                (
                    source_device_deployment_id IS NULL
                    AND source_device_command_id IS NULL
                )
                OR
                (
                    scope_kind = 'ORGANIZATION'
                    AND source_device_deployment_id IS NOT NULL
                    AND source_device_command_id IS NOT NULL
                )
            )
        )
    ),
    CONSTRAINT ck_ops_task_payload CHECK (
        payload_schema_version BETWEEN 1 AND 2147483647
        AND JSON_TYPE(redacted_execution_snapshot) = 'OBJECT'
        AND OCTET_LENGTH(CAST(redacted_execution_snapshot AS CHAR)) <= 262144
    ),
    CONSTRAINT ck_ops_task_state CHECK (
        state IN ('PENDING', 'DONE', 'CANCELLED', 'BLOCKED')
    ),
    CONSTRAINT ck_ops_task_lease_group CHECK (
        (
            lease_token IS NULL
            AND lease_worker IS NULL
            AND lease_until IS NULL
        )
        OR
        (
            lease_token IS NOT NULL
            AND lease_worker IS NOT NULL
            AND lease_until IS NOT NULL
            AND lease_token REGEXP
                '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        )
    ),
    CONSTRAINT ck_ops_task_state_shape CHECK (
        (
            state = 'PENDING'
            AND next_run_at IS NOT NULL
            AND completed_at IS NULL
            AND blocked_reason_code IS NULL
            AND blocked_diagnostic IS NULL
        )
        OR
        (
            state IN ('DONE', 'CANCELLED')
            AND next_run_at IS NULL
            AND lease_token IS NULL
            AND lease_worker IS NULL
            AND lease_until IS NULL
            AND completed_at IS NOT NULL
            AND blocked_reason_code IS NULL
            AND blocked_diagnostic IS NULL
            AND handled_wake_version = wake_version
        )
        OR
        (
            state = 'BLOCKED'
            AND next_run_at IS NULL
            AND lease_token IS NULL
            AND lease_worker IS NULL
            AND lease_until IS NULL
            AND completed_at IS NOT NULL
            AND blocked_reason_code IS NOT NULL
            AND handled_wake_version = wake_version
        )
    ),
    CONSTRAINT ck_ops_task_values CHECK (
        priority BETWEEN -32768 AND 32767
        AND retry_policy_version BETWEEN 1 AND 2147483647
        AND max_auto_attempts BETWEEN 1 AND 1000
        AND attempt_sequence BETWEEN 0 AND 9007199254740991
        AND consecutive_failure_count >= 0
        AND wake_version BETWEEN 0 AND 9007199254740991
        AND handled_wake_version BETWEEN 0 AND wake_version
        AND lock_version >= 0
    ),
    CONSTRAINT ck_ops_task_times CHECK (
        updated_at >= created_at
        AND (next_run_at IS NULL OR next_run_at >= created_at)
        AND (lease_until IS NULL OR lease_until > created_at)
        AND (completed_at IS NULL OR completed_at >= created_at)
    ),
    CONSTRAINT fk_ops_task_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES iam_tenant (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_task_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_task_inbox_identity
        FOREIGN KEY (source_inbox_id)
        REFERENCES ops_inbox_message (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_task_inbox_scope_kind
        FOREIGN KEY (scope_kind, source_inbox_id)
        REFERENCES ops_inbox_message (scope_kind, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_task_inbox_tenant_scope
        FOREIGN KEY (scope_kind, tenant_id, source_inbox_id)
        REFERENCES ops_inbox_message (scope_kind, tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_task_inbox_org_scope
        FOREIGN KEY (
            scope_kind,
            tenant_id,
            organization_id,
            source_inbox_id
        )
        REFERENCES ops_inbox_message (
            scope_kind,
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_task_device_command
        FOREIGN KEY (
            tenant_id,
            organization_id,
            source_device_deployment_id,
            source_device_command_id
        )
        REFERENCES dev_device_command (
            tenant_id,
            organization_id,
            deployment_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_task_initiating_audit
        FOREIGN KEY (initiating_audit_id)
        REFERENCES ops_audit_log (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_ops_task_tenant_fk (tenant_id),
    INDEX ix_ops_task_org_fk (tenant_id, organization_id),
    INDEX ix_ops_task_inbox_identity_fk (source_inbox_id),
    INDEX ix_ops_task_inbox_scope_kind_fk (scope_kind, source_inbox_id),
    INDEX ix_ops_task_inbox_tenant_fk (
        scope_kind,
        tenant_id,
        source_inbox_id
    ),
    INDEX ix_ops_task_inbox_org_fk (
        scope_kind,
        tenant_id,
        organization_id,
        source_inbox_id
    ),
    INDEX ix_ops_task_device_command_fk (
        tenant_id,
        organization_id,
        source_device_deployment_id,
        source_device_command_id
    ),
    INDEX ix_ops_task_initiating_audit_fk (initiating_audit_id),
    INDEX ix_ops_task_claim (
        state,
        execution_lane,
        claimable_at,
        priority,
        id
    ),
    INDEX ix_ops_task_target (
        target_type,
        target_stable_key,
        id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE ops_task_attempt (
    id BIGINT NOT NULL AUTO_INCREMENT,
    attempt_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    task_id BIGINT NOT NULL,
    scope_kind VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NULL,
    organization_id BIGINT NULL,
    attempt_no BIGINT NOT NULL,
    lease_token CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    claimed_wake_version BIGINT NOT NULL,
    worker_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    claimed_at DATETIME(3) NOT NULL,
    lease_until DATETIME(3) NOT NULL,
    external_call_may_have_started_at DATETIME(3) NULL,
    reclaimed_at DATETIME(3) NULL,
    result_recorded_at DATETIME(3) NULL,
    action_kind VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    technical_result VARCHAR(48) CHARACTER SET ascii COLLATE ascii_bin NULL,
    request_sha256 BINARY(32) NULL,
    response_sha256 BINARY(32) NULL,
    http_status INT NULL,
    external_api_error_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    duration_ms BIGINT NULL,
    redacted_diagnostic VARCHAR(1000) NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_ops_attempt_uid UNIQUE (attempt_uid),
    CONSTRAINT uq_ops_attempt_task_no UNIQUE (task_id, attempt_no),
    CONSTRAINT uq_ops_attempt_lease_token UNIQUE (lease_token),
    CONSTRAINT uq_ops_attempt_scope_kind_id UNIQUE (scope_kind, id),
    CONSTRAINT uq_ops_attempt_scope_tenant_id
        UNIQUE (scope_kind, tenant_id, id),
    CONSTRAINT uq_ops_attempt_scope_org_id
        UNIQUE (scope_kind, tenant_id, organization_id, id),
    CONSTRAINT ck_ops_attempt_uids_v4 CHECK (
        attempt_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND lease_token REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_ops_attempt_scope CHECK (
        (
            scope_kind = 'PLATFORM'
            AND tenant_id IS NULL
            AND organization_id IS NULL
        )
        OR
        (
            scope_kind = 'TENANT'
            AND tenant_id IS NOT NULL
            AND organization_id IS NULL
        )
        OR
        (
            scope_kind = 'ORGANIZATION'
            AND tenant_id IS NOT NULL
            AND organization_id IS NOT NULL
        )
    ),
    CONSTRAINT ck_ops_attempt_action CHECK (
        action_kind IN ('PROCESS', 'SUBMIT', 'QUERY', 'CLOSE', 'CANCEL')
    ),
    CONSTRAINT ck_ops_attempt_result CHECK (
        technical_result IS NULL
        OR technical_result IN (
            'TECHNICAL_SUCCESS',
            'NO_ACTION_REQUIRED',
            'RETRYABLE_FAILURE',
            'OUTCOME_UNKNOWN',
            'PERMANENT_TECHNICAL_FAILURE'
        )
    ),
    CONSTRAINT ck_ops_attempt_result_group CHECK (
        (
            result_recorded_at IS NULL
            AND technical_result IS NULL
            AND response_sha256 IS NULL
            AND http_status IS NULL
            AND external_api_error_code IS NULL
            AND duration_ms IS NULL
        )
        OR
        (
            result_recorded_at IS NOT NULL
            AND technical_result IS NOT NULL
            AND duration_ms IS NOT NULL
            AND duration_ms >= 0
        )
    ),
    CONSTRAINT ck_ops_attempt_values CHECK (
        attempt_no BETWEEN 1 AND 9007199254740991
        AND claimed_wake_version BETWEEN 0 AND 9007199254740991
        AND (http_status IS NULL OR http_status BETWEEN 100 AND 599)
    ),
    CONSTRAINT ck_ops_attempt_times CHECK (
        claimed_at >= created_at
        AND lease_until > claimed_at
        AND (
            external_call_may_have_started_at IS NULL
            OR external_call_may_have_started_at >= claimed_at
        )
        AND (reclaimed_at IS NULL OR reclaimed_at >= claimed_at)
        AND (
            result_recorded_at IS NULL
            OR result_recorded_at >= claimed_at
        )
    ),
    CONSTRAINT fk_ops_attempt_task_identity
        FOREIGN KEY (task_id)
        REFERENCES ops_reliable_task (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_attempt_task_scope_kind
        FOREIGN KEY (scope_kind, task_id)
        REFERENCES ops_reliable_task (scope_kind, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_attempt_task_tenant_scope
        FOREIGN KEY (scope_kind, tenant_id, task_id)
        REFERENCES ops_reliable_task (scope_kind, tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_attempt_task_org_scope
        FOREIGN KEY (scope_kind, tenant_id, organization_id, task_id)
        REFERENCES ops_reliable_task (
            scope_kind,
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_ops_attempt_task_identity_fk (task_id),
    INDEX ix_ops_attempt_task_scope_kind_fk (scope_kind, task_id),
    INDEX ix_ops_attempt_task_tenant_fk (
        scope_kind,
        tenant_id,
        task_id
    ),
    INDEX ix_ops_attempt_task_org_fk (
        scope_kind,
        tenant_id,
        organization_id,
        task_id
    ),
    INDEX ix_ops_attempt_task_timeline (
        task_id,
        attempt_no,
        claimed_at,
        id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE ops_alert (
    id BIGINT NOT NULL AUTO_INCREMENT,
    alert_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    scope_kind VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NULL,
    organization_id BIGINT NULL,
    alert_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    category VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    current_severity VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    highest_severity VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_kind VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_type VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_key VARCHAR(160) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    aggregation_key BINARY(32) NOT NULL,
    status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    first_seen_at DATETIME(3) NOT NULL,
    last_seen_at DATETIME(3) NOT NULL,
    discovery_count BIGINT NOT NULL DEFAULT 1,
    safe_display_parameters JSON NOT NULL,
    acknowledged_at DATETIME(3) NULL,
    acknowledged_audit_id BIGINT NULL,
    resolved_at DATETIME(3) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    active_aggregation_key BINARY(32)
        GENERATED ALWAYS AS (
            CASE WHEN status = 'OPEN' THEN aggregation_key ELSE NULL END
        ) STORED,
    PRIMARY KEY (id),
    CONSTRAINT uq_ops_alert_uid UNIQUE (alert_uid),
    CONSTRAINT uq_ops_alert_source
        UNIQUE (source_kind, source_type, source_key),
    CONSTRAINT uq_ops_alert_active_aggregation UNIQUE (active_aggregation_key),
    CONSTRAINT uq_ops_alert_ack_audit UNIQUE (acknowledged_audit_id),
    CONSTRAINT uq_ops_alert_scope_kind_id UNIQUE (scope_kind, id),
    CONSTRAINT uq_ops_alert_scope_tenant_id
        UNIQUE (scope_kind, tenant_id, id),
    CONSTRAINT uq_ops_alert_scope_org_id
        UNIQUE (scope_kind, tenant_id, organization_id, id),
    CONSTRAINT ck_ops_alert_uid_v4 CHECK (
        alert_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_ops_alert_scope CHECK (
        (
            scope_kind IN ('PLATFORM', 'UNRESOLVED')
            AND tenant_id IS NULL
            AND organization_id IS NULL
        )
        OR
        (
            scope_kind = 'TENANT'
            AND tenant_id IS NOT NULL
            AND organization_id IS NULL
        )
        OR
        (
            scope_kind = 'ORGANIZATION'
            AND tenant_id IS NOT NULL
            AND organization_id IS NOT NULL
        )
    ),
    CONSTRAINT ck_ops_alert_severity CHECK (
        current_severity IN ('INFO', 'WARNING', 'CRITICAL')
        AND highest_severity IN ('INFO', 'WARNING', 'CRITICAL')
        AND (
            highest_severity = 'CRITICAL'
            OR (
                highest_severity = 'WARNING'
                AND current_severity IN ('INFO', 'WARNING')
            )
            OR (
                highest_severity = 'INFO'
                AND current_severity = 'INFO'
            )
        )
    ),
    CONSTRAINT ck_ops_alert_source CHECK (
        source_kind IN ('DOMAIN_FACT', 'TECHNICAL_CONDITION')
        AND CHAR_LENGTH(TRIM(source_type)) > 0
        AND CHAR_LENGTH(TRIM(source_key)) > 0
    ),
    CONSTRAINT ck_ops_alert_status CHECK (
        (
            status = 'OPEN'
            AND resolved_at IS NULL
        )
        OR
        (
            status = 'RESOLVED'
            AND resolved_at IS NOT NULL
        )
    ),
    CONSTRAINT ck_ops_alert_ack CHECK (
        (
            acknowledged_at IS NULL
            AND acknowledged_audit_id IS NULL
        )
        OR
        (
            acknowledged_at IS NOT NULL
            AND acknowledged_audit_id IS NOT NULL
        )
    ),
    CONSTRAINT ck_ops_alert_values CHECK (
        discovery_count BETWEEN 1 AND 9007199254740991
        AND lock_version >= 0
        AND JSON_TYPE(safe_display_parameters) = 'OBJECT'
    ),
    CONSTRAINT ck_ops_alert_times CHECK (
        first_seen_at >= created_at
        AND last_seen_at >= first_seen_at
        AND (acknowledged_at IS NULL OR acknowledged_at >= first_seen_at)
        AND (resolved_at IS NULL OR resolved_at >= first_seen_at)
        AND updated_at >= created_at
    ),
    CONSTRAINT fk_ops_alert_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES iam_tenant (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_alert_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_alert_ack_audit
        FOREIGN KEY (acknowledged_audit_id)
        REFERENCES ops_audit_log (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_ops_alert_tenant_fk (tenant_id),
    INDEX ix_ops_alert_org_fk (tenant_id, organization_id),
    INDEX ix_ops_alert_ack_audit_fk (acknowledged_audit_id),
    INDEX ix_ops_alert_scope_queue (
        scope_kind,
        tenant_id,
        organization_id,
        status,
        current_severity,
        last_seen_at,
        id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE ops_reconciliation_run (
    id BIGINT NOT NULL AUTO_INCREMENT,
    run_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    scope_kind VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin
        NOT NULL DEFAULT 'PLATFORM',
    run_type VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    merchant_profile_id BIGINT NOT NULL,
    channel_business_date DATE NOT NULL,
    snapshot_cutoff_at DATETIME(3) NOT NULL,
    coordinator_task_id BIGINT NOT NULL,
    state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    started_at DATETIME(3) NULL,
    last_progress_at DATETIME(3) NULL,
    completed_at DATETIME(3) NULL,
    checked_recharge_count BIGINT NOT NULL DEFAULT 0,
    checked_withdrawal_count BIGINT NOT NULL DEFAULT 0,
    deterministic_repair_count BIGINT NOT NULL DEFAULT 0,
    issue_count_at_completion BIGINT NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_ops_reconciliation_run_uid UNIQUE (run_uid),
    CONSTRAINT uq_ops_reconciliation_run_day
        UNIQUE (run_type, merchant_profile_id, channel_business_date),
    CONSTRAINT uq_ops_reconciliation_run_task UNIQUE (coordinator_task_id),
    CONSTRAINT uq_ops_reconciliation_run_scope_id UNIQUE (scope_kind, id),
    CONSTRAINT ck_ops_reconciliation_run_uid_v4 CHECK (
        run_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_ops_reconciliation_run_scope CHECK (
        scope_kind = 'PLATFORM'
        AND run_type = 'WECHAT_DAILY_TRADE'
    ),
    CONSTRAINT ck_ops_reconciliation_run_state CHECK (
        (
            state = 'CREATED'
            AND started_at IS NULL
            AND last_progress_at IS NULL
            AND completed_at IS NULL
            AND issue_count_at_completion IS NULL
        )
        OR
        (
            state = 'RUNNING'
            AND started_at IS NOT NULL
            AND last_progress_at IS NOT NULL
            AND completed_at IS NULL
            AND issue_count_at_completion IS NULL
        )
        OR
        (
            state = 'COMPLETED'
            AND started_at IS NOT NULL
            AND last_progress_at IS NOT NULL
            AND completed_at IS NOT NULL
            AND issue_count_at_completion IS NOT NULL
        )
    ),
    CONSTRAINT ck_ops_reconciliation_run_counts CHECK (
        checked_recharge_count >= 0
        AND checked_withdrawal_count >= 0
        AND deterministic_repair_count >= 0
        AND (
            issue_count_at_completion IS NULL
            OR issue_count_at_completion >= 0
        )
        AND lock_version >= 0
    ),
    CONSTRAINT ck_ops_reconciliation_run_times CHECK (
        snapshot_cutoff_at >= created_at
        AND (started_at IS NULL OR started_at >= created_at)
        AND (
            last_progress_at IS NULL
            OR last_progress_at >= started_at
        )
        AND (completed_at IS NULL OR completed_at >= started_at)
        AND updated_at >= created_at
    ),
    CONSTRAINT fk_ops_reconciliation_run_merchant
        FOREIGN KEY (merchant_profile_id)
        REFERENCES fund_wechat_merchant_profile (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_reconciliation_run_task
        FOREIGN KEY (scope_kind, coordinator_task_id)
        REFERENCES ops_reliable_task (scope_kind, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_ops_reconciliation_run_merchant_fk (merchant_profile_id),
    INDEX ix_ops_reconciliation_run_task_fk (
        scope_kind,
        coordinator_task_id
    ),
    INDEX ix_ops_reconciliation_run_state (
        state,
        channel_business_date,
        id
    ),
    INDEX ix_ops_reconciliation_run_merchant_date (
        merchant_profile_id,
        channel_business_date,
        id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE ops_reconciliation_issue (
    id BIGINT NOT NULL AUTO_INCREMENT,
    issue_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    scope_kind VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NULL,
    organization_id BIGINT NULL,
    issue_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    severity VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    subject_type VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    subject_stable_key VARCHAR(160) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    dedupe_key BINARY(32) NOT NULL,
    state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    first_seen_run_id BIGINT NOT NULL,
    latest_seen_run_id BIGINT NOT NULL,
    first_seen_at DATETIME(3) NOT NULL,
    last_seen_at DATETIME(3) NOT NULL,
    discovery_count BIGINT NOT NULL DEFAULT 1,
    initial_evidence_sha256 BINARY(32) NOT NULL,
    redacted_evidence_summary VARCHAR(2000) NOT NULL,
    last_handled_at DATETIME(3) NULL,
    last_handled_audit_id BIGINT NULL,
    system_verified_resolved_at DATETIME(3) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    active_dedupe_key BINARY(32)
        GENERATED ALWAYS AS (
            CASE WHEN state = 'UNRESOLVED' THEN dedupe_key ELSE NULL END
        ) STORED,
    PRIMARY KEY (id),
    CONSTRAINT uq_ops_reconciliation_issue_uid UNIQUE (issue_uid),
    CONSTRAINT uq_ops_reconciliation_issue_active UNIQUE (active_dedupe_key),
    CONSTRAINT uq_ops_reconciliation_issue_scope_kind_id UNIQUE (scope_kind, id),
    CONSTRAINT uq_ops_reconciliation_issue_scope_tenant_id
        UNIQUE (scope_kind, tenant_id, id),
    CONSTRAINT uq_ops_reconciliation_issue_scope_org_id
        UNIQUE (scope_kind, tenant_id, organization_id, id),
    CONSTRAINT ck_ops_reconciliation_issue_uid_v4 CHECK (
        issue_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_ops_reconciliation_issue_scope CHECK (
        (
            scope_kind IN ('PLATFORM', 'UNRESOLVED')
            AND tenant_id IS NULL
            AND organization_id IS NULL
        )
        OR
        (
            scope_kind = 'TENANT'
            AND tenant_id IS NOT NULL
            AND organization_id IS NULL
        )
        OR
        (
            scope_kind = 'ORGANIZATION'
            AND tenant_id IS NOT NULL
            AND organization_id IS NOT NULL
        )
    ),
    CONSTRAINT ck_ops_reconciliation_issue_state CHECK (
        (
            state = 'UNRESOLVED'
            AND system_verified_resolved_at IS NULL
        )
        OR
        (
            state = 'RESOLVED'
            AND system_verified_resolved_at IS NOT NULL
        )
    ),
    CONSTRAINT ck_ops_reconciliation_issue_severity CHECK (
        severity IN ('INFO', 'WARNING', 'CRITICAL')
    ),
    CONSTRAINT ck_ops_reconciliation_issue_handled CHECK (
        (
            last_handled_at IS NULL
            AND last_handled_audit_id IS NULL
        )
        OR
        (
            last_handled_at IS NOT NULL
            AND last_handled_audit_id IS NOT NULL
        )
    ),
    CONSTRAINT ck_ops_reconciliation_issue_values CHECK (
        discovery_count BETWEEN 1 AND 9007199254740991
        AND lock_version >= 0
        AND CHAR_LENGTH(TRIM(subject_type)) > 0
        AND CHAR_LENGTH(TRIM(subject_stable_key)) > 0
        AND CHAR_LENGTH(redacted_evidence_summary) BETWEEN 1 AND 2000
    ),
    CONSTRAINT ck_ops_reconciliation_issue_times CHECK (
        first_seen_at >= created_at
        AND last_seen_at >= first_seen_at
        AND (
            last_handled_at IS NULL
            OR last_handled_at >= first_seen_at
        )
        AND (
            system_verified_resolved_at IS NULL
            OR system_verified_resolved_at >= first_seen_at
        )
        AND updated_at >= created_at
    ),
    CONSTRAINT fk_ops_reconciliation_issue_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES iam_tenant (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_reconciliation_issue_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_reconciliation_issue_first_run
        FOREIGN KEY (first_seen_run_id)
        REFERENCES ops_reconciliation_run (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_reconciliation_issue_latest_run
        FOREIGN KEY (latest_seen_run_id)
        REFERENCES ops_reconciliation_run (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_reconciliation_issue_handled_audit
        FOREIGN KEY (last_handled_audit_id)
        REFERENCES ops_audit_log (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_ops_reconciliation_issue_tenant_fk (tenant_id),
    INDEX ix_ops_reconciliation_issue_org_fk (tenant_id, organization_id),
    INDEX ix_ops_reconciliation_issue_first_run_fk (first_seen_run_id),
    INDEX ix_ops_reconciliation_issue_latest_run_fk (latest_seen_run_id),
    INDEX ix_ops_reconciliation_issue_audit_fk (last_handled_audit_id),
    INDEX ix_ops_reconciliation_issue_queue (
        scope_kind,
        tenant_id,
        organization_id,
        state,
        severity,
        last_seen_at,
        id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE ops_reconciliation_action (
    id BIGINT NOT NULL AUTO_INCREMENT,
    action_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    action_key VARCHAR(255) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    scope_kind VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NULL,
    organization_id BIGINT NULL,
    reconciliation_issue_id BIGINT NULL,
    reconciliation_run_id BIGINT NULL,
    action_type VARCHAR(40) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_kind VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_audit_id BIGINT NULL,
    source_task_attempt_id BIGINT NULL,
    reliable_task_id BIGINT NULL,
    result_fact_type VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    result_fact_key VARCHAR(160) CHARACTER SET ascii COLLATE ascii_bin NULL,
    redacted_note VARCHAR(1000) NULL,
    result_sha256 BINARY(32) NULL,
    occurred_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_ops_reconciliation_action_uid UNIQUE (action_uid),
    CONSTRAINT uq_ops_reconciliation_action_key UNIQUE (action_key),
    CONSTRAINT uq_ops_reconciliation_action_scope_kind_id
        UNIQUE (scope_kind, id),
    CONSTRAINT uq_ops_reconciliation_action_scope_tenant_id
        UNIQUE (scope_kind, tenant_id, id),
    CONSTRAINT uq_ops_reconciliation_action_scope_org_id
        UNIQUE (scope_kind, tenant_id, organization_id, id),
    CONSTRAINT ck_ops_reconciliation_action_uid_v4 CHECK (
        action_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_ops_reconciliation_action_key CHECK (
        action_key REGEXP '^[A-Z0-9_:-]{8,255}$'
    ),
    CONSTRAINT ck_ops_reconciliation_action_scope CHECK (
        (
            scope_kind IN ('PLATFORM', 'UNRESOLVED')
            AND tenant_id IS NULL
            AND organization_id IS NULL
        )
        OR
        (
            scope_kind = 'TENANT'
            AND tenant_id IS NOT NULL
            AND organization_id IS NULL
        )
        OR
        (
            scope_kind = 'ORGANIZATION'
            AND tenant_id IS NOT NULL
            AND organization_id IS NOT NULL
        )
    ),
    CONSTRAINT ck_ops_reconciliation_action_type CHECK (
        action_type IN (
            'AUTO_REPAIR_APPLIED',
            'NOTE_ADDED',
            'MARK_HANDLED',
            'CHANNEL_QUERY_REQUESTED',
            'CONTROLLED_RETRY_REQUESTED',
            'RESOLUTION_VERIFIED'
        )
    ),
    CONSTRAINT ck_ops_reconciliation_action_source CHECK (
        (
            source_kind = 'RECONCILIATION_RUN'
            AND reconciliation_run_id IS NOT NULL
            AND source_audit_id IS NULL
            AND source_task_attempt_id IS NULL
        )
        OR
        (
            source_kind = 'HUMAN_AUDIT'
            AND reconciliation_run_id IS NULL
            AND source_audit_id IS NOT NULL
            AND source_task_attempt_id IS NULL
        )
        OR
        (
            source_kind = 'TASK_ATTEMPT'
            AND reconciliation_run_id IS NULL
            AND source_audit_id IS NULL
            AND source_task_attempt_id IS NOT NULL
        )
    ),
    CONSTRAINT ck_ops_reconciliation_action_result CHECK (
        (
            result_fact_type IS NULL
            AND result_fact_key IS NULL
        )
        OR
        (
            result_fact_type IS NOT NULL
            AND result_fact_key IS NOT NULL
            AND CHAR_LENGTH(TRIM(result_fact_type)) > 0
            AND CHAR_LENGTH(TRIM(result_fact_key)) > 0
        )
    ),
    CONSTRAINT ck_ops_reconciliation_action_times CHECK (
        occurred_at >= created_at
    ),
    CONSTRAINT fk_ops_reconciliation_action_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES iam_tenant (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_reconciliation_action_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_reconciliation_action_issue
        FOREIGN KEY (reconciliation_issue_id)
        REFERENCES ops_reconciliation_issue (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_reconciliation_action_run
        FOREIGN KEY (reconciliation_run_id)
        REFERENCES ops_reconciliation_run (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_reconciliation_action_audit
        FOREIGN KEY (source_audit_id)
        REFERENCES ops_audit_log (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_reconciliation_action_attempt
        FOREIGN KEY (source_task_attempt_id)
        REFERENCES ops_task_attempt (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ops_reconciliation_action_task
        FOREIGN KEY (reliable_task_id)
        REFERENCES ops_reliable_task (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_ops_reconciliation_action_tenant_fk (tenant_id),
    INDEX ix_ops_reconciliation_action_org_fk (tenant_id, organization_id),
    INDEX ix_ops_reconciliation_action_issue_fk (reconciliation_issue_id),
    INDEX ix_ops_reconciliation_action_run_fk (reconciliation_run_id),
    INDEX ix_ops_reconciliation_action_audit_fk (source_audit_id),
    INDEX ix_ops_reconciliation_action_attempt_fk (source_task_attempt_id),
    INDEX ix_ops_reconciliation_action_task_fk (reliable_task_id),
    INDEX ix_ops_reconciliation_action_issue_time (
        reconciliation_issue_id,
        occurred_at,
        id
    ),
    INDEX ix_ops_reconciliation_action_run_time (
        reconciliation_run_id,
        occurred_at,
        id
    ),
    INDEX ix_ops_reconciliation_action_task_time (
        reliable_task_id,
        occurred_at,
        id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
