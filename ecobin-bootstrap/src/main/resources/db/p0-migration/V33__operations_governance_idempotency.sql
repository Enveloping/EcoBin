-- Linearize governance write idempotency before locking the business target.
-- This table intentionally stores only public actor identity and fingerprints;
-- no identity-module BIGINT key crosses the operations boundary here.

CREATE TABLE ops_governance_idempotency (
    operation_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    actor_kind VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    actor_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    scope_sha256 BINARY(32) NOT NULL,
    action_code VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    target_type VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    target_stable_key VARCHAR(160) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    request_sha256 BINARY(32) NOT NULL,
    status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    result_resource_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    result_state VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    result_version BIGINT NULL,
    created_at DATETIME(3) NOT NULL,
    completed_at DATETIME(3) NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (operation_uid),
    CONSTRAINT ck_ops_governance_idempotency_operation_uid CHECK (
        operation_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_ops_governance_idempotency_actor_uid CHECK (
        actor_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_ops_governance_idempotency_status CHECK (
        status IN ('IN_PROGRESS', 'SUCCEEDED')
        AND (
            (status = 'IN_PROGRESS'
             AND result_resource_uid IS NULL
             AND result_state IS NULL
             AND result_version IS NULL
             AND completed_at IS NULL)
            OR
            (status = 'SUCCEEDED'
             AND result_resource_uid IS NOT NULL
             AND result_state IS NOT NULL
             AND result_version IS NOT NULL
             AND completed_at IS NOT NULL)
        )
    ),
    CONSTRAINT ck_ops_governance_idempotency_result_uid CHECK (
        result_resource_uid IS NULL
        OR result_resource_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_ops_governance_idempotency_time CHECK (
        updated_at >= created_at
        AND (completed_at IS NULL OR completed_at >= created_at)
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
