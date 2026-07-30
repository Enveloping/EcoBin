-- A photo terminal event is reliable independently of delivery/clean
-- completion. Keep the authenticated fact even when its business work record
-- has not arrived yet, then link it to the concrete photo slot later.
CREATE TABLE rec_photo_terminal_fact (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    edge_event_id BIGINT NOT NULL,
    edge_event_type VARCHAR(48)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    work_type VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    work_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    position VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    status VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    photo_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    object_url VARCHAR(1500) CHARACTER SET ascii COLLATE ascii_bin NULL,
    sha256 BINARY(32) NULL,
    size_bytes BIGINT NULL,
    captured_at DATETIME(3) NULL,
    missing_reason VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NULL,
    device_occurred_at DATETIME(3) NULL,
    backend_received_at DATETIME(3) NOT NULL,
    delivery_photo_id BIGINT NULL,
    clean_photo_id BIGINT NULL,
    linked_at DATETIME(3) NULL,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_rec_photo_terminal_edge UNIQUE (edge_event_id),
    CONSTRAINT uq_rec_photo_terminal_work_slot UNIQUE (
        tenant_id,
        organization_id,
        work_type,
        work_uid,
        position
    ),
    CONSTRAINT uq_rec_photo_terminal_scope_id UNIQUE (
        tenant_id,
        organization_id,
        deployment_id,
        id
    ),
    CONSTRAINT ck_rec_photo_terminal_edge_type
        CHECK (edge_event_type = 'PHOTO_STATUS_REPORTED'),
    CONSTRAINT ck_rec_photo_terminal_work CHECK (
        (
            work_type = 'DELIVERY_SESSION'
            AND position IN (
                'BEFORE_INNER',
                'BEFORE_OUTER',
                'AFTER_INNER',
                'AFTER_OUTER'
            )
        )
        OR
        (
            work_type = 'CLEAN_OPERATION'
            AND position IN (
                'FIRST_OPEN_INNER',
                'FIRST_OPEN_OUTER',
                'FINAL_CLOSE_INNER',
                'FINAL_CLOSE_OUTER'
            )
        )
    ),
    CONSTRAINT ck_rec_photo_terminal_uid CHECK (
        work_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND (
            photo_uid IS NULL
            OR photo_uid REGEXP
                '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        )
    ),
    CONSTRAINT ck_rec_photo_terminal_state CHECK (
        (
            status = 'AVAILABLE'
            AND photo_uid IS NOT NULL
            AND object_url IS NOT NULL
            AND sha256 IS NOT NULL
            AND size_bytes > 0
            AND captured_at IS NOT NULL
            AND missing_reason IS NULL
        )
        OR
        (
            status = 'PERMANENTLY_MISSING'
            AND object_url IS NULL
            AND missing_reason IS NOT NULL
            AND missing_reason REGEXP '^[A-Z][A-Z0-9_]{0,63}$'
            AND (
                (
                    photo_uid IS NULL
                    AND sha256 IS NULL
                    AND size_bytes IS NULL
                    AND captured_at IS NULL
                )
                OR
                (
                    photo_uid IS NOT NULL
                    AND sha256 IS NOT NULL
                    AND size_bytes > 0
                    AND captured_at IS NOT NULL
                )
            )
        )
    ),
    CONSTRAINT ck_rec_photo_terminal_url CHECK (
        object_url IS NULL
        OR (
            object_url REGEXP '^https://[^/?#]+/ecobin/.+\\.jpg$'
            AND INSTR(object_url, '?') = 0
            AND INSTR(object_url, '#') = 0
        )
    ),
    CONSTRAINT ck_rec_photo_terminal_link CHECK (
        (
            delivery_photo_id IS NULL
            AND clean_photo_id IS NULL
            AND linked_at IS NULL
        )
        OR
        (
            work_type = 'DELIVERY_SESSION'
            AND delivery_photo_id IS NOT NULL
            AND clean_photo_id IS NULL
            AND linked_at IS NOT NULL
        )
        OR
        (
            work_type = 'CLEAN_OPERATION'
            AND delivery_photo_id IS NULL
            AND clean_photo_id IS NOT NULL
            AND linked_at IS NOT NULL
        )
    ),
    CONSTRAINT ck_rec_photo_terminal_times CHECK (
        updated_at >= created_at
        AND backend_received_at <= created_at
        AND (
            device_occurred_at IS NULL
            OR backend_received_at >= device_occurred_at
        )
        AND (linked_at IS NULL OR linked_at >= created_at)
    ),
    CONSTRAINT fk_rec_photo_terminal_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_photo_terminal_deployment
        FOREIGN KEY (tenant_id, organization_id, deployment_id)
        REFERENCES dev_device_deployment (
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_photo_terminal_edge
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            edge_event_id,
            edge_event_type
        )
        REFERENCES dev_edge_event (
            tenant_id,
            organization_id,
            deployment_id,
            id,
            event_type
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_photo_terminal_delivery_photo
        FOREIGN KEY (
            tenant_id,
            organization_id,
            delivery_photo_id
        )
        REFERENCES rec_delivery_photo (
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_photo_terminal_clean_photo
        FOREIGN KEY (
            tenant_id,
            organization_id,
            clean_photo_id
        )
        REFERENCES rec_clean_photo (
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_photo_terminal_unlinked (
        tenant_id,
        organization_id,
        work_type,
        work_uid,
        linked_at
    ),
    INDEX ix_rec_photo_terminal_deployment_fk (
        tenant_id,
        organization_id,
        deployment_id
    ),
    INDEX ix_rec_photo_terminal_delivery_photo_fk (
        tenant_id,
        organization_id,
        delivery_photo_id
    ),
    INDEX ix_rec_photo_terminal_clean_photo_fk (
        tenant_id,
        organization_id,
        clean_photo_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
    COLLATE=utf8mb4_0900_ai_ci;

-- The request contains only stable business identities. COS temporary
-- credentials are deliberately absent and are minted at outbound call time.
CREATE TABLE dev_photo_upload_grant_request (
    id BIGINT NOT NULL AUTO_INCREMENT,
    event_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    edge_event_id BIGINT NOT NULL,
    edge_event_type VARCHAR(48)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    work_type VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    work_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    grant_generation BIGINT NOT NULL,
    requested_slots JSON NOT NULL,
    request_reason VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    command_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    reliable_task_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    request_status VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    device_occurred_at DATETIME(3) NULL,
    backend_received_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_photo_grant_request_event UNIQUE (event_uid),
    CONSTRAINT uq_dev_photo_grant_request_edge UNIQUE (edge_event_id),
    CONSTRAINT uq_dev_photo_grant_request_generation UNIQUE (
        tenant_id,
        organization_id,
        deployment_id,
        work_type,
        work_uid,
        grant_generation
    ),
    CONSTRAINT uq_dev_photo_grant_request_command UNIQUE (command_uid),
    CONSTRAINT uq_dev_photo_grant_request_task UNIQUE (reliable_task_uid),
    CONSTRAINT ck_dev_photo_grant_request_edge_type
        CHECK (edge_event_type = 'PHOTO_UPLOAD_GRANT_REQUESTED'),
    CONSTRAINT ck_dev_photo_grant_request_work CHECK (
        work_type IN ('DELIVERY_SESSION', 'CLEAN_OPERATION')
        AND work_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND grant_generation > 0
        AND JSON_TYPE(requested_slots) = 'ARRAY'
        AND JSON_LENGTH(requested_slots) BETWEEN 1 AND 4
    ),
    CONSTRAINT ck_dev_photo_grant_request_reason CHECK (
        request_reason IN (
            'INITIAL_GRANT_MISSING',
            'GRANT_EXPIRED',
            'EDGE_RESTARTED',
            'UPLOAD_RETRY'
        )
    ),
    CONSTRAINT ck_dev_photo_grant_request_status CHECK (
        (
            request_status = 'TASK_CREATED'
            AND command_uid IS NOT NULL
            AND reliable_task_uid IS NOT NULL
        )
        OR
        (
            request_status = 'NO_ACTION_REQUIRED'
            AND command_uid IS NULL
            AND reliable_task_uid IS NULL
        )
        OR
        request_status = 'SUPERSEDED'
    ),
    CONSTRAINT ck_dev_photo_grant_request_uids CHECK (
        event_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND (
            command_uid IS NULL
            OR command_uid REGEXP
                '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        )
        AND (
            reliable_task_uid IS NULL
            OR reliable_task_uid REGEXP
                '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        )
    ),
    CONSTRAINT ck_dev_photo_grant_request_times CHECK (
        updated_at >= created_at
        AND backend_received_at <= created_at
        AND (
            device_occurred_at IS NULL
            OR backend_received_at >= device_occurred_at
        )
    ),
    CONSTRAINT fk_dev_photo_grant_request_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_photo_grant_request_deployment
        FOREIGN KEY (tenant_id, organization_id, deployment_id)
        REFERENCES dev_device_deployment (
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_photo_grant_request_edge
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            edge_event_id,
            edge_event_type
        )
        REFERENCES dev_edge_event (
            tenant_id,
            organization_id,
            deployment_id,
            id,
            event_type
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_photo_grant_request_task
        FOREIGN KEY (reliable_task_uid)
        REFERENCES ops_reliable_task (task_uid)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_photo_grant_request_work (
        tenant_id,
        organization_id,
        deployment_id,
        work_type,
        work_uid,
        grant_generation
    ),
    INDEX ix_dev_photo_grant_request_deployment_fk (
        tenant_id,
        organization_id,
        deployment_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
    COLLATE=utf8mb4_0900_ai_ci;

-- Clean completion uses the same pending-photo forms as delivery completion:
-- a slot may be uncaptured, or captured locally but not uploaded yet.
ALTER TABLE rec_clean_photo
    DROP CHECK ck_rec_clean_photo_state;

UPDATE rec_clean_photo
SET missing_reason = 'UPLOAD_PENDING'
WHERE status = 'UPLOAD_PENDING'
  AND missing_reason IS NULL;

ALTER TABLE rec_clean_photo
    ADD CONSTRAINT ck_rec_clean_photo_state CHECK (
        (
            status = 'UPLOAD_PENDING'
            AND object_url IS NULL
            AND linked_at IS NULL
            AND missing_reason IS NOT NULL
            AND missing_reason REGEXP '^[A-Z][A-Z0-9_]{0,63}$'
            AND (
                (
                    sha256 IS NULL
                    AND size_bytes IS NULL
                )
                OR
                (
                    photo_uid IS NOT NULL
                    AND sha256 IS NOT NULL
                    AND size_bytes > 0
                )
            )
        )
        OR
        (
            status = 'AVAILABLE'
            AND photo_uid IS NOT NULL
            AND object_url IS NOT NULL
            AND sha256 IS NOT NULL
            AND size_bytes > 0
            AND linked_at IS NOT NULL
            AND missing_reason IS NULL
        )
        OR
        (
            status = 'PERMANENTLY_MISSING'
            AND object_url IS NULL
            AND linked_at IS NOT NULL
            AND missing_reason IS NOT NULL
            AND missing_reason REGEXP '^[A-Z][A-Z0-9_]{0,63}$'
            AND (
                (
                    sha256 IS NULL
                    AND size_bytes IS NULL
                )
                OR
                (
                    photo_uid IS NOT NULL
                    AND sha256 IS NOT NULL
                    AND size_bytes > 0
                )
            )
        )
    );

-- The edge reuses one stable event identity for each command stage.
ALTER TABLE dev_device_command_event
    ADD CONSTRAINT uq_dev_command_event_stage
        UNIQUE (command_id, observation_stage);
