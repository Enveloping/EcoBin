-- EcoBin P0 recycling facts.
-- V5 creates exactly 24 tables. It contains no seed data and deliberately
-- keeps foreign-key checks enabled throughout the migration.

CREATE TABLE rec_organization_delivery_config (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    version_no BIGINT NOT NULL,
    content_sha256 BINARY(32) NOT NULL,
    review_mode VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    open_balance_floor_cent BIGINT NOT NULL,
    max_review_abs_weight_g BIGINT NOT NULL,
    publication_source VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    published_by_staff_account_id BIGINT NULL,
    published_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_rec_delivery_config_org_version
        UNIQUE (tenant_id, organization_id, version_no),
    CONSTRAINT uq_rec_delivery_config_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_rec_delivery_config_scope_version_id
        UNIQUE (tenant_id, organization_id, id, version_no),
    CONSTRAINT uq_rec_delivery_config_session_ref
        UNIQUE (
            tenant_id,
            organization_id,
            id,
            version_no,
            content_sha256,
            open_balance_floor_cent,
            max_review_abs_weight_g
        ),
    CONSTRAINT ck_rec_delivery_config_version
        CHECK (version_no BETWEEN 1 AND 9007199254740991),
    CONSTRAINT ck_rec_delivery_config_m0 CHECK (
        review_mode = 'ALL_MANUAL'
        AND open_balance_floor_cent < 0
        AND max_review_abs_weight_g BETWEEN 1 AND 1000000
    ),
    CONSTRAINT ck_rec_delivery_config_publisher CHECK (
        (
            publication_source = 'STAFF'
            AND published_by_staff_account_id IS NOT NULL
        )
        OR
        (
            publication_source = 'SYSTEM'
            AND published_by_staff_account_id IS NULL
        )
    ),
    CONSTRAINT ck_rec_delivery_config_times CHECK (
        published_at >= created_at
    ),
    CONSTRAINT fk_rec_delivery_config_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_delivery_config_publisher
        FOREIGN KEY (tenant_id, published_by_staff_account_id)
        REFERENCES iam_staff_account (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_delivery_config_org_version (
        tenant_id,
        organization_id,
        version_no DESC
    ),
    INDEX ix_rec_delivery_config_publisher_fk (
        tenant_id,
        published_by_staff_account_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE rec_organization_delivery_config_head (
    organization_id BIGINT NOT NULL,
    tenant_id BIGINT NOT NULL,
    current_config_id BIGINT NOT NULL,
    current_version_no BIGINT NOT NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    switched_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (organization_id),
    CONSTRAINT uq_rec_delivery_head_scope
        UNIQUE (tenant_id, organization_id),
    CONSTRAINT ck_rec_delivery_head_versions CHECK (
        current_version_no BETWEEN 1 AND 9007199254740991
        AND lock_version >= 0
    ),
    CONSTRAINT ck_rec_delivery_head_times CHECK (updated_at >= switched_at),
    CONSTRAINT fk_rec_delivery_head_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_delivery_head_config
        FOREIGN KEY (
            tenant_id,
            organization_id,
            current_config_id,
            current_version_no
        )
        REFERENCES rec_organization_delivery_config (
            tenant_id,
            organization_id,
            id,
            version_no
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_delivery_head_config_fk (
        tenant_id,
        organization_id,
        current_config_id,
        current_version_no
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE rec_organization_order_counter (
    organization_id BIGINT NOT NULL,
    tenant_id BIGINT NOT NULL,
    last_visibility_sequence_no BIGINT NOT NULL DEFAULT 0,
    lock_version BIGINT NOT NULL DEFAULT 0,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (organization_id),
    CONSTRAINT uq_rec_order_counter_scope
        UNIQUE (tenant_id, organization_id),
    CONSTRAINT ck_rec_order_counter_values CHECK (
        last_visibility_sequence_no BETWEEN 0 AND 9007199254740991
        AND lock_version >= 0
    ),
    CONSTRAINT fk_rec_order_counter_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_order_counter_org_fk (tenant_id, organization_id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE rec_organization_clean_config (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    version_no BIGINT NOT NULL,
    content_sha256 BINARY(32) NOT NULL,
    review_mode VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    operation_timeout_seconds INT NOT NULL DEFAULT 1800,
    publication_source VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    published_by_staff_account_id BIGINT NULL,
    published_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_rec_clean_config_org_version
        UNIQUE (tenant_id, organization_id, version_no),
    CONSTRAINT uq_rec_clean_config_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_rec_clean_config_scope_version_id
        UNIQUE (tenant_id, organization_id, id, version_no),
    CONSTRAINT uq_rec_clean_config_operation_ref
        UNIQUE (
            tenant_id,
            organization_id,
            id,
            version_no,
            operation_timeout_seconds
        ),
    CONSTRAINT ck_rec_clean_config_version
        CHECK (version_no BETWEEN 1 AND 9007199254740991),
    CONSTRAINT ck_rec_clean_config_m0 CHECK (
        review_mode = 'ALL_MANUAL'
        AND operation_timeout_seconds = 1800
    ),
    CONSTRAINT ck_rec_clean_config_publisher CHECK (
        (
            publication_source = 'STAFF'
            AND published_by_staff_account_id IS NOT NULL
        )
        OR
        (
            publication_source = 'SYSTEM'
            AND published_by_staff_account_id IS NULL
        )
    ),
    CONSTRAINT ck_rec_clean_config_times CHECK (
        published_at >= created_at
    ),
    CONSTRAINT fk_rec_clean_config_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_clean_config_publisher
        FOREIGN KEY (tenant_id, published_by_staff_account_id)
        REFERENCES iam_staff_account (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_clean_config_org_version (
        tenant_id,
        organization_id,
        version_no DESC
    ),
    INDEX ix_rec_clean_config_publisher_fk (
        tenant_id,
        published_by_staff_account_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE rec_organization_clean_config_head (
    organization_id BIGINT NOT NULL,
    tenant_id BIGINT NOT NULL,
    current_config_id BIGINT NOT NULL,
    current_version_no BIGINT NOT NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    switched_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (organization_id),
    CONSTRAINT uq_rec_clean_head_scope
        UNIQUE (tenant_id, organization_id),
    CONSTRAINT ck_rec_clean_head_versions CHECK (
        current_version_no BETWEEN 1 AND 9007199254740991
        AND lock_version >= 0
    ),
    CONSTRAINT ck_rec_clean_head_times CHECK (updated_at >= switched_at),
    CONSTRAINT fk_rec_clean_head_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_clean_head_config
        FOREIGN KEY (
            tenant_id,
            organization_id,
            current_config_id,
            current_version_no
        )
        REFERENCES rec_organization_clean_config (
            tenant_id,
            organization_id,
            id,
            version_no
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_clean_head_config_fk (
        tenant_id,
        organization_id,
        current_config_id,
        current_version_no
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE rec_organization_clean_record_counter (
    organization_id BIGINT NOT NULL,
    tenant_id BIGINT NOT NULL,
    last_visibility_sequence_no BIGINT NOT NULL DEFAULT 0,
    lock_version BIGINT NOT NULL DEFAULT 0,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (organization_id),
    CONSTRAINT uq_rec_clean_counter_scope
        UNIQUE (tenant_id, organization_id),
    CONSTRAINT ck_rec_clean_counter_values CHECK (
        last_visibility_sequence_no BETWEEN 0 AND 9007199254740991
        AND lock_version >= 0
    ),
    CONSTRAINT fk_rec_clean_counter_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_clean_counter_org_fk (tenant_id, organization_id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE rec_bag (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    bag_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    registered_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_rec_bag_code UNIQUE (bag_code),
    CONSTRAINT uq_rec_bag_scope_id UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT ck_rec_bag_code CHECK (
        bag_code REGEXP '^[A-Za-z0-9_-]{8,64}$'
    ),
    CONSTRAINT ck_rec_bag_times CHECK (registered_at >= created_at),
    CONSTRAINT fk_rec_bag_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_bag_org_registered (
        tenant_id,
        organization_id,
        registered_at,
        id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE dev_delivery_session
    ADD CONSTRAINT uq_dev_delivery_session_order_ref UNIQUE (
        tenant_id,
        organization_id,
        deployment_id,
        port_id,
        id,
        organization_user_id,
        delivery_config_version_id,
        delivery_config_content_sha256,
        unit_price_yuan_per_kg,
        open_balance_floor_cent,
        max_review_abs_weight_g,
        negative_weight_anomaly_threshold_g,
        bag_id,
        bag_code_snapshot
    );

ALTER TABLE dev_port_config_snapshot
    ADD CONSTRAINT uq_dev_port_config_recycling_ref UNIQUE (
        tenant_id,
        organization_id,
        deployment_id,
        config_version_id,
        port_id,
        id
    );

CREATE TABLE rec_delivery_order (
    id BIGINT NOT NULL AUTO_INCREMENT,
    delivery_order_no VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    visibility_sequence_no BIGINT NOT NULL,
    delivery_session_id BIGINT NOT NULL,
    physical_result_id BIGINT NOT NULL,
    organization_user_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    port_id BIGINT NOT NULL,
    device_config_version_id BIGINT NOT NULL,
    delivery_config_version_id BIGINT NOT NULL,
    delivery_config_version_no BIGINT NOT NULL,
    delivery_config_content_sha256 BINARY(32) NOT NULL,
    unit_price_yuan_per_kg DECIMAL(15, 4) NOT NULL,
    open_balance_floor_cent BIGINT NOT NULL,
    bag_id BIGINT NOT NULL,
    bag_code_snapshot VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    negative_weight_anomaly_threshold_g BIGINT NOT NULL,
    max_review_abs_weight_g BIGINT NOT NULL,
    initial_weight_status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    initial_weight_g BIGINT NULL,
    final_weight_status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    final_weight_g BIGINT NULL,
    raw_net_weight_g BIGINT NULL,
    negative_weight_anomaly TINYINT NOT NULL,
    raw_business_weight_kg DECIMAL(15, 2) NULL,
    raw_amount_cent BIGINT NULL,
    raw_calculation_status VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    review_status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    current_revision_no BIGINT NOT NULL DEFAULT 0,
    current_revision_id BIGINT NULL,
    final_business_weight_kg DECIMAL(15, 2) NULL,
    final_amount_cent BIGINT NULL,
    first_approved_at DATETIME(3) NULL,
    device_occurred_at DATETIME(3) NOT NULL,
    backend_received_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_rec_delivery_order_no UNIQUE (delivery_order_no),
    CONSTRAINT uq_rec_delivery_order_visibility
        UNIQUE (tenant_id, organization_id, visibility_sequence_no),
    CONSTRAINT uq_rec_delivery_order_session UNIQUE (delivery_session_id),
    CONSTRAINT uq_rec_delivery_order_result UNIQUE (physical_result_id),
    CONSTRAINT uq_rec_delivery_order_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_rec_delivery_order_result_ref
        UNIQUE (
            tenant_id,
            organization_id,
            id,
            physical_result_id
        ),
    CONSTRAINT uq_rec_delivery_order_port_id
        UNIQUE (tenant_id, organization_id, port_id, id),
    CONSTRAINT uq_rec_delivery_order_detection_ref
        UNIQUE (tenant_id, organization_id, port_id, id, bag_id),
    CONSTRAINT ck_rec_delivery_order_no CHECK (
        BINARY delivery_order_no = BINARY TRIM(delivery_order_no)
        AND CHAR_LENGTH(delivery_order_no) BETWEEN 8 AND 64
    ),
    CONSTRAINT ck_rec_delivery_order_snapshots CHECK (
        visibility_sequence_no BETWEEN 1 AND 9007199254740991
        AND unit_price_yuan_per_kg > 0
        AND open_balance_floor_cent < 0
        AND negative_weight_anomaly_threshold_g > 0
        AND max_review_abs_weight_g BETWEEN 1 AND 1000000
        AND bag_code_snapshot REGEXP '^[A-Za-z0-9_-]{8,64}$'
        AND negative_weight_anomaly IN (0, 1)
    ),
    CONSTRAINT ck_rec_delivery_order_weight_shape CHECK (
        initial_weight_status IN ('RELIABLE', 'INVALID', 'UNAVAILABLE')
        AND final_weight_status IN ('RELIABLE', 'INVALID', 'UNAVAILABLE')
        AND (
            initial_weight_status <> 'RELIABLE'
            OR initial_weight_g IS NOT NULL
        )
        AND (
            initial_weight_status <> 'UNAVAILABLE'
            OR initial_weight_g IS NULL
        )
        AND (
            final_weight_status <> 'RELIABLE'
            OR final_weight_g IS NOT NULL
        )
        AND (
            final_weight_status <> 'UNAVAILABLE'
            OR final_weight_g IS NULL
        )
    ),
    CONSTRAINT ck_rec_delivery_order_raw_shape CHECK (
        raw_calculation_status IN ('RELIABLE', 'INVALID', 'UNAVAILABLE')
        AND (
            (
                raw_calculation_status = 'RELIABLE'
                AND raw_net_weight_g IS NOT NULL
                AND raw_business_weight_kg IS NOT NULL
                AND raw_amount_cent IS NOT NULL
            )
            OR
            (
                raw_calculation_status IN ('INVALID', 'UNAVAILABLE')
                AND raw_amount_cent IS NULL
            )
        )
    ),
    CONSTRAINT ck_rec_delivery_order_review_shape CHECK (
        (
            review_status = 'PENDING'
            AND current_revision_no = 0
            AND current_revision_id IS NULL
            AND final_business_weight_kg IS NULL
            AND final_amount_cent IS NULL
            AND first_approved_at IS NULL
        )
        OR
        (
            review_status = 'APPROVED'
            AND current_revision_no >= 1
            AND current_revision_id IS NOT NULL
            AND final_business_weight_kg IS NOT NULL
            AND final_amount_cent IS NOT NULL
            AND first_approved_at IS NOT NULL
        )
    ),
    CONSTRAINT ck_rec_delivery_order_times CHECK (
        backend_received_at >= device_occurred_at
        AND created_at >= backend_received_at
        AND updated_at >= created_at
        AND (
            first_approved_at IS NULL
            OR first_approved_at >= created_at
        )
    ),
    CONSTRAINT fk_rec_delivery_order_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_delivery_order_session
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            port_id,
            delivery_session_id
        )
        REFERENCES dev_delivery_session (
            tenant_id,
            organization_id,
            deployment_id,
            port_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_delivery_order_session_snapshot
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            port_id,
            delivery_session_id,
            organization_user_id,
            delivery_config_version_id,
            delivery_config_content_sha256,
            unit_price_yuan_per_kg,
            open_balance_floor_cent,
            max_review_abs_weight_g,
            negative_weight_anomaly_threshold_g,
            bag_id,
            bag_code_snapshot
        )
        REFERENCES dev_delivery_session (
            tenant_id,
            organization_id,
            deployment_id,
            port_id,
            id,
            organization_user_id,
            delivery_config_version_id,
            delivery_config_content_sha256,
            unit_price_yuan_per_kg,
            open_balance_floor_cent,
            max_review_abs_weight_g,
            negative_weight_anomaly_threshold_g,
            bag_id,
            bag_code_snapshot
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_delivery_order_result
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            port_id,
            physical_result_id,
            delivery_session_id
        )
        REFERENCES dev_physical_result (
            tenant_id,
            organization_id,
            deployment_id,
            port_id,
            id,
            delivery_session_id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_delivery_order_user
        FOREIGN KEY (tenant_id, organization_id, organization_user_id)
        REFERENCES iam_organization_user (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_delivery_order_device_config
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            device_config_version_id
        )
        REFERENCES dev_config_version (
            tenant_id,
            organization_id,
            deployment_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_delivery_order_delivery_config
        FOREIGN KEY (
            tenant_id,
            organization_id,
            delivery_config_version_id,
            delivery_config_version_no,
            delivery_config_content_sha256,
            open_balance_floor_cent,
            max_review_abs_weight_g
        )
        REFERENCES rec_organization_delivery_config (
            tenant_id,
            organization_id,
            id,
            version_no,
            content_sha256,
            open_balance_floor_cent,
            max_review_abs_weight_g
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_delivery_order_bag
        FOREIGN KEY (tenant_id, organization_id, bag_id)
        REFERENCES rec_bag (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_delivery_order_review (
        tenant_id,
        organization_id,
        review_status,
        device_occurred_at,
        delivery_order_no
    ),
    INDEX ix_rec_delivery_order_user_history (
        tenant_id,
        organization_id,
        organization_user_id,
        device_occurred_at,
        delivery_order_no
    ),
    INDEX ix_rec_delivery_order_deployment (
        tenant_id,
        organization_id,
        deployment_id,
        port_id,
        device_occurred_at,
        delivery_order_no
    ),
    INDEX ix_rec_delivery_order_bag_history (
        tenant_id,
        organization_id,
        bag_id,
        device_occurred_at,
        delivery_order_no
    ),
    INDEX ix_rec_delivery_order_result_fk (
        tenant_id,
        organization_id,
        deployment_id,
        port_id,
        physical_result_id,
        delivery_session_id
    ),
    INDEX ix_rec_delivery_order_session_fk (
        tenant_id,
        organization_id,
        deployment_id,
        port_id,
        delivery_session_id
    ),
    INDEX ix_rec_delivery_order_session_snapshot_fk (
        tenant_id,
        organization_id,
        deployment_id,
        port_id,
        delivery_session_id,
        organization_user_id,
        delivery_config_version_id,
        delivery_config_content_sha256,
        unit_price_yuan_per_kg,
        open_balance_floor_cent,
        max_review_abs_weight_g,
        negative_weight_anomaly_threshold_g,
        bag_id,
        bag_code_snapshot
    ),
    INDEX ix_rec_delivery_order_config_fk (
        tenant_id,
        organization_id,
        delivery_config_version_id,
        delivery_config_version_no,
        delivery_config_content_sha256,
        open_balance_floor_cent,
        max_review_abs_weight_g
    ),
    INDEX ix_rec_delivery_order_device_config_fk (
        tenant_id,
        organization_id,
        deployment_id,
        device_config_version_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE rec_delivery_anomaly (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    delivery_order_id BIGINT NOT NULL,
    source_physical_result_id BIGINT NOT NULL,
    category VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    anomaly_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    diagnostic_json JSON NULL,
    detected_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_rec_delivery_anomaly_code
        UNIQUE (delivery_order_id, anomaly_code),
    CONSTRAINT uq_rec_delivery_anomaly_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT ck_rec_delivery_anomaly_category
        CHECK (category IN ('USER', 'SYSTEM')),
    CONSTRAINT ck_rec_delivery_anomaly_code CHECK (
        BINARY anomaly_code = BINARY TRIM(anomaly_code)
        AND CHAR_LENGTH(anomaly_code) BETWEEN 1 AND 64
        AND (
            category <> 'USER'
            OR anomaly_code = 'NEGATIVE_WEIGHT_ANOMALY'
        )
    ),
    CONSTRAINT ck_rec_delivery_anomaly_times
        CHECK (detected_at >= created_at),
    CONSTRAINT fk_rec_delivery_anomaly_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_delivery_anomaly_order_result
        FOREIGN KEY (
            tenant_id,
            organization_id,
            delivery_order_id,
            source_physical_result_id
        )
        REFERENCES rec_delivery_order (
            tenant_id,
            organization_id,
            id,
            physical_result_id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_delivery_anomaly_org_time (
        tenant_id,
        organization_id,
        anomaly_code,
        detected_at,
        id
    ),
    INDEX ix_rec_delivery_anomaly_order_fk (
        tenant_id,
        organization_id,
        delivery_order_id,
        source_physical_result_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE rec_delivery_revision (
    id BIGINT NOT NULL AUTO_INCREMENT,
    revision_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    delivery_order_id BIGINT NOT NULL,
    revision_no BIGINT NOT NULL,
    previous_revision_id BIGINT NULL,
    previous_revision_no BIGINT NULL,
    revision_type VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    decision_type VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    before_final_weight_kg DECIMAL(15, 2) NULL,
    before_final_amount_cent BIGINT NULL,
    after_final_weight_kg DECIMAL(15, 2) NOT NULL,
    after_final_amount_cent BIGINT NOT NULL,
    amount_delta_cent BIGINT NOT NULL,
    reviewer_kind VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    platform_admin_id BIGINT NULL,
    staff_account_id BIGINT NULL,
    reason VARCHAR(500) NULL,
    request_sha256 BINARY(32) NOT NULL,
    reviewed_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_rec_delivery_revision_uid UNIQUE (revision_uid),
    CONSTRAINT uq_rec_delivery_revision_order_no
        UNIQUE (delivery_order_id, revision_no),
    CONSTRAINT uq_rec_delivery_revision_successor
        UNIQUE (previous_revision_id),
    CONSTRAINT uq_rec_delivery_revision_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_rec_delivery_revision_current_ref UNIQUE (
        tenant_id,
        organization_id,
        delivery_order_id,
        id,
        revision_no,
        after_final_weight_kg,
        after_final_amount_cent
    ),
    CONSTRAINT uq_rec_delivery_revision_previous_ref UNIQUE (
        delivery_order_id,
        id,
        revision_no,
        after_final_weight_kg,
        after_final_amount_cent
    ),
    CONSTRAINT ck_rec_delivery_revision_uid_v4 CHECK (
        revision_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_rec_delivery_revision_chain CHECK (
        (
            revision_type = 'INITIAL_REVIEW'
            AND revision_no = 1
            AND previous_revision_id IS NULL
            AND previous_revision_no IS NULL
            AND before_final_weight_kg IS NULL
            AND before_final_amount_cent IS NULL
        )
        OR
        (
            revision_type = 'CORRECTION'
            AND revision_no >= 2
            AND previous_revision_id IS NOT NULL
            AND previous_revision_no = revision_no - 1
            AND before_final_weight_kg IS NOT NULL
            AND before_final_amount_cent IS NOT NULL
        )
    ),
    CONSTRAINT ck_rec_delivery_revision_decision CHECK (
        decision_type IN ('ORIGINAL_APPROVED', 'MODIFIED_APPROVED')
    ),
    CONSTRAINT ck_rec_delivery_revision_delta CHECK (
        CAST(amount_delta_cent AS DECIMAL(20, 0))
        =
        CAST(after_final_amount_cent AS DECIMAL(20, 0))
        -
        CAST(COALESCE(before_final_amount_cent, 0) AS DECIMAL(20, 0))
    ),
    CONSTRAINT ck_rec_delivery_revision_actor CHECK (
        (
            reviewer_kind = 'PLATFORM_ADMIN'
            AND platform_admin_id IS NOT NULL
            AND staff_account_id IS NULL
        )
        OR
        (
            reviewer_kind = 'STAFF'
            AND platform_admin_id IS NULL
            AND staff_account_id IS NOT NULL
        )
    ),
    CONSTRAINT ck_rec_delivery_revision_reason CHECK (
        reason IS NULL OR CHAR_LENGTH(TRIM(reason)) > 0
    ),
    CONSTRAINT ck_rec_delivery_revision_times
        CHECK (reviewed_at >= created_at),
    CONSTRAINT fk_rec_delivery_revision_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_delivery_revision_order
        FOREIGN KEY (tenant_id, organization_id, delivery_order_id)
        REFERENCES rec_delivery_order (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_delivery_revision_platform
        FOREIGN KEY (platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_delivery_revision_staff
        FOREIGN KEY (tenant_id, staff_account_id)
        REFERENCES iam_staff_account (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_delivery_revision_previous
        FOREIGN KEY (
            delivery_order_id,
            previous_revision_id,
            previous_revision_no,
            before_final_weight_kg,
            before_final_amount_cent
        )
        REFERENCES rec_delivery_revision (
            delivery_order_id,
            id,
            revision_no,
            after_final_weight_kg,
            after_final_amount_cent
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_delivery_revision_order (
        tenant_id,
        organization_id,
        delivery_order_id,
        revision_no
    ),
    INDEX ix_rec_delivery_revision_platform_fk (platform_admin_id),
    INDEX ix_rec_delivery_revision_staff_fk (tenant_id, staff_account_id),
    INDEX ix_rec_delivery_revision_previous_fk (
        delivery_order_id,
        previous_revision_id,
        previous_revision_no,
        before_final_weight_kg,
        before_final_amount_cent
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE rec_delivery_order
    ADD CONSTRAINT fk_rec_delivery_order_current_revision
        FOREIGN KEY (
            tenant_id,
            organization_id,
            id,
            current_revision_id,
            current_revision_no,
            final_business_weight_kg,
            final_amount_cent
        )
        REFERENCES rec_delivery_revision (
            tenant_id,
            organization_id,
            delivery_order_id,
            id,
            revision_no,
            after_final_weight_kg,
            after_final_amount_cent
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_rec_delivery_order_current_revision_fk (
        tenant_id,
        organization_id,
        id,
        current_revision_id,
        current_revision_no,
        final_business_weight_kg,
        final_amount_cent
    );

CREATE TABLE rec_delivery_photo (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    delivery_order_id BIGINT NOT NULL,
    position VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    photo_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    status VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    object_url VARCHAR(1500) CHARACTER SET ascii COLLATE ascii_bin NULL,
    sha256 BINARY(32) NULL,
    size_bytes BIGINT NULL,
    captured_at DATETIME(3) NULL,
    linked_at DATETIME(3) NULL,
    missing_reason VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NULL,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_rec_delivery_photo_position
        UNIQUE (delivery_order_id, position),
    CONSTRAINT uq_rec_delivery_photo_uid UNIQUE (photo_uid),
    CONSTRAINT uq_rec_delivery_photo_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT ck_rec_delivery_photo_position CHECK (
        position IN (
            'BEFORE_INNER',
            'BEFORE_OUTER',
            'AFTER_INNER',
            'AFTER_OUTER'
        )
    ),
    CONSTRAINT ck_rec_delivery_photo_uid CHECK (
        photo_uid IS NULL
        OR photo_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_rec_delivery_photo_state CHECK (
        (
            status = 'UPLOAD_PENDING'
            AND object_url IS NULL
            AND sha256 IS NULL
            AND size_bytes IS NULL
            AND linked_at IS NULL
            AND missing_reason IS NULL
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
            AND sha256 IS NULL
            AND size_bytes IS NULL
            AND linked_at IS NOT NULL
            AND missing_reason IS NOT NULL
            AND CHAR_LENGTH(TRIM(missing_reason)) > 0
        )
    ),
    CONSTRAINT ck_rec_delivery_photo_url CHECK (
        object_url IS NULL
        OR (
            object_url REGEXP '^https://[^/?#]+/ecobin/.+\\.jpg$'
            AND INSTR(object_url, '?') = 0
            AND INSTR(object_url, '#') = 0
        )
    ),
    CONSTRAINT ck_rec_delivery_photo_times CHECK (
        updated_at >= created_at
        AND (captured_at IS NULL OR captured_at <= updated_at)
        AND (linked_at IS NULL OR linked_at >= created_at)
    ),
    CONSTRAINT fk_rec_delivery_photo_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_delivery_photo_order
        FOREIGN KEY (tenant_id, organization_id, delivery_order_id)
        REFERENCES rec_delivery_order (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_delivery_photo_pending (
        tenant_id,
        organization_id,
        status,
        updated_at,
        id
    ),
    INDEX ix_rec_delivery_photo_order_fk (
        tenant_id,
        organization_id,
        delivery_order_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE rec_clean_operation (
    id BIGINT NOT NULL AUTO_INCREMENT,
    operation_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    port_id BIGINT NOT NULL,
    cleaner_organization_user_id BIGINT NOT NULL,
    device_config_version_id BIGINT NOT NULL,
    clean_config_version_id BIGINT NOT NULL,
    clean_config_version_no BIGINT NOT NULL,
    operation_timeout_seconds INT NOT NULL,
    old_bag_binding_state VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    old_bag_id BIGINT NULL,
    old_bag_code_snapshot VARCHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    old_baseline_state VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    old_baseline_id BIGINT NULL,
    old_baseline_weight_g BIGINT NULL,
    pre_unlock_weight_status VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    pre_unlock_weight_g BIGINT NULL,
    pre_unlock_weight_fault_code VARCHAR(100)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    new_bag_id BIGINT NOT NULL,
    new_bag_code_snapshot VARCHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    pending_delivery_result_session_id BIGINT NULL,
    status VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    start_authorization_expires_at DATETIME(3) NOT NULL,
    edge_saved_at DATETIME(3) NULL,
    first_possible_unlock_at DATETIME(3) NULL,
    solenoid_powered_off_at DATETIME(3) NULL,
    cleaner_confirmed_closed_at DATETIME(3) NULL,
    execution_deadline_at DATETIME(3) NULL,
    pre_unlock_end_requested_at DATETIME(3) NULL,
    recovery_requested_at DATETIME(3) NULL,
    reopen_count INT NOT NULL DEFAULT 0,
    recovery_count INT NOT NULL DEFAULT 0,
    completion_record_id BIGINT NULL,
    ended_at DATETIME(3) NULL,
    end_reason VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    active_port_id BIGINT
        GENERATED ALWAYS AS (
            CASE
                WHEN status IN (
                    'PREPARED',
                    'EDGE_SAVED',
                    'IN_PROGRESS',
                    'RECOVERY_REQUIRED'
                )
                THEN port_id
                ELSE NULL
            END
        ) STORED,
    PRIMARY KEY (id),
    CONSTRAINT uq_rec_clean_operation_uid UNIQUE (operation_uid),
    CONSTRAINT uq_rec_clean_operation_active_port UNIQUE (active_port_id),
    CONSTRAINT uq_rec_clean_operation_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_rec_clean_operation_port_id
        UNIQUE (tenant_id, organization_id, deployment_id, port_id, id),
    CONSTRAINT uq_rec_clean_operation_reservation_ref UNIQUE (
        tenant_id,
        organization_id,
        id,
        new_bag_id,
        port_id
    ),
    CONSTRAINT ck_rec_clean_operation_uid_v4 CHECK (
        operation_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_rec_clean_operation_status CHECK (
        status IN (
            'PREPARED',
            'EDGE_SAVED',
            'IN_PROGRESS',
            'RECOVERY_REQUIRED',
            'PRE_UNLOCK_ENDED',
            'COMPLETED'
        )
    ),
    CONSTRAINT ck_rec_clean_operation_bag_shape CHECK (
        (
            (
                old_bag_binding_state = 'BOUND'
                AND old_bag_id IS NOT NULL
                AND old_bag_code_snapshot IS NOT NULL
                AND old_bag_code_snapshot REGEXP '^[A-Za-z0-9_-]{8,64}$'
            )
            OR
            (
                old_bag_binding_state = 'MISSING'
                AND old_bag_id IS NULL
                AND old_bag_code_snapshot IS NULL
            )
        )
        AND new_bag_code_snapshot REGEXP '^[A-Za-z0-9_-]{8,64}$'
        AND (old_bag_id IS NULL OR old_bag_id <> new_bag_id)
    ),
    CONSTRAINT ck_rec_clean_operation_baseline_shape CHECK (
        (
            old_baseline_state = 'TRUSTED'
            AND old_bag_binding_state = 'BOUND'
            AND old_baseline_id IS NOT NULL
            AND old_baseline_weight_g IS NOT NULL
            AND old_baseline_weight_g >= 0
        )
        OR
        (
            old_baseline_state = 'UNTRUSTED'
            AND (
                (
                    old_baseline_id IS NULL
                    AND old_baseline_weight_g IS NULL
                )
                OR
                (
                    old_baseline_id IS NOT NULL
                    AND old_baseline_weight_g IS NOT NULL
                    AND old_baseline_weight_g >= 0
                )
            )
        )
        OR
        (
            old_baseline_state = 'MISSING'
            AND old_baseline_id IS NULL
            AND old_baseline_weight_g IS NULL
        )
    ),
    CONSTRAINT ck_rec_clean_operation_pre_weight CHECK (
        (
            pre_unlock_weight_status = 'PENDING'
            AND pre_unlock_weight_g IS NULL
            AND pre_unlock_weight_fault_code IS NULL
        )
        OR
        (
            pre_unlock_weight_status = 'RELIABLE'
            AND pre_unlock_weight_g IS NOT NULL
            AND pre_unlock_weight_fault_code IS NULL
        )
        OR
        (
            pre_unlock_weight_status = 'FAILED'
            AND pre_unlock_weight_g IS NULL
            AND pre_unlock_weight_fault_code IS NOT NULL
            AND CHAR_LENGTH(TRIM(pre_unlock_weight_fault_code)) > 0
        )
    ),
    CONSTRAINT ck_rec_clean_operation_state_shape CHECK (
        (
            status = 'PREPARED'
            AND edge_saved_at IS NULL
            AND first_possible_unlock_at IS NULL
            AND completion_record_id IS NULL
            AND ended_at IS NULL
            AND end_reason IS NULL
        )
        OR
        (
            status = 'EDGE_SAVED'
            AND edge_saved_at IS NOT NULL
            AND execution_deadline_at IS NOT NULL
            AND first_possible_unlock_at IS NULL
            AND completion_record_id IS NULL
            AND ended_at IS NULL
            AND end_reason IS NULL
        )
        OR
        (
            status = 'IN_PROGRESS'
            AND edge_saved_at IS NOT NULL
            AND execution_deadline_at IS NOT NULL
            AND first_possible_unlock_at IS NOT NULL
            AND completion_record_id IS NULL
            AND ended_at IS NULL
            AND end_reason IS NULL
        )
        OR
        (
            status = 'RECOVERY_REQUIRED'
            AND edge_saved_at IS NOT NULL
            AND first_possible_unlock_at IS NOT NULL
            AND completion_record_id IS NULL
            AND ended_at IS NULL
            AND end_reason IS NULL
        )
        OR
        (
            status = 'PRE_UNLOCK_ENDED'
            AND first_possible_unlock_at IS NULL
            AND solenoid_powered_off_at IS NULL
            AND cleaner_confirmed_closed_at IS NULL
            AND completion_record_id IS NULL
            AND ended_at IS NOT NULL
            AND end_reason IS NOT NULL
        )
        OR
        (
            status = 'COMPLETED'
            AND edge_saved_at IS NOT NULL
            AND first_possible_unlock_at IS NOT NULL
            AND solenoid_powered_off_at IS NOT NULL
            AND cleaner_confirmed_closed_at IS NOT NULL
            AND completion_record_id IS NOT NULL
            AND ended_at IS NOT NULL
            AND end_reason IS NOT NULL
        )
    ),
    CONSTRAINT ck_rec_clean_operation_values CHECK (
        operation_timeout_seconds = 1800
        AND reopen_count >= 0
        AND recovery_count >= 0
        AND lock_version >= 0
    ),
    CONSTRAINT ck_rec_clean_operation_times CHECK (
        updated_at >= created_at
        AND start_authorization_expires_at > created_at
        AND (edge_saved_at IS NULL OR edge_saved_at >= created_at)
        AND (
            execution_deadline_at IS NULL
            OR (
                edge_saved_at IS NOT NULL
                AND execution_deadline_at > edge_saved_at
            )
        )
        AND (
            first_possible_unlock_at IS NULL
            OR (
                edge_saved_at IS NOT NULL
                AND first_possible_unlock_at >= edge_saved_at
            )
        )
        AND (
            solenoid_powered_off_at IS NULL
            OR (
                first_possible_unlock_at IS NOT NULL
                AND solenoid_powered_off_at >= first_possible_unlock_at
            )
        )
        AND (
            cleaner_confirmed_closed_at IS NULL
            OR (
                first_possible_unlock_at IS NOT NULL
                AND cleaner_confirmed_closed_at >= first_possible_unlock_at
            )
        )
        AND (
            pre_unlock_end_requested_at IS NULL
            OR pre_unlock_end_requested_at >= created_at
        )
        AND (
            recovery_requested_at IS NULL
            OR first_possible_unlock_at IS NOT NULL
        )
        AND (ended_at IS NULL OR ended_at >= created_at)
    ),
    CONSTRAINT fk_rec_clean_operation_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_clean_operation_port
        FOREIGN KEY (tenant_id, organization_id, deployment_id, port_id)
        REFERENCES dev_port (tenant_id, organization_id, deployment_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_clean_operation_cleaner
        FOREIGN KEY (
            tenant_id,
            organization_id,
            cleaner_organization_user_id
        )
        REFERENCES iam_organization_user (
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_clean_operation_device_config
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            device_config_version_id
        )
        REFERENCES dev_config_version (
            tenant_id,
            organization_id,
            deployment_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_clean_operation_clean_config
        FOREIGN KEY (
            tenant_id,
            organization_id,
            clean_config_version_id,
            clean_config_version_no,
            operation_timeout_seconds
        )
        REFERENCES rec_organization_clean_config (
            tenant_id,
            organization_id,
            id,
            version_no,
            operation_timeout_seconds
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_clean_operation_old_bag
        FOREIGN KEY (tenant_id, organization_id, old_bag_id)
        REFERENCES rec_bag (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_clean_operation_new_bag
        FOREIGN KEY (tenant_id, organization_id, new_bag_id)
        REFERENCES rec_bag (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_clean_operation_pending_session
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            port_id,
            pending_delivery_result_session_id
        )
        REFERENCES dev_delivery_session (
            tenant_id,
            organization_id,
            deployment_id,
            port_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_clean_operation_status_deadline (
        tenant_id,
        organization_id,
        status,
        execution_deadline_at,
        id
    ),
    INDEX ix_rec_clean_operation_cleaner_history (
        tenant_id,
        organization_id,
        cleaner_organization_user_id,
        created_at,
        id
    ),
    INDEX ix_rec_clean_operation_port_fk (
        tenant_id,
        organization_id,
        deployment_id,
        port_id
    ),
    INDEX ix_rec_clean_operation_device_config_fk (
        tenant_id,
        organization_id,
        deployment_id,
        device_config_version_id
    ),
    INDEX ix_rec_clean_operation_config_fk (
        tenant_id,
        organization_id,
        clean_config_version_id,
        clean_config_version_no,
        operation_timeout_seconds
    ),
    INDEX ix_rec_clean_operation_old_bag_fk (
        tenant_id,
        organization_id,
        old_bag_id
    ),
    INDEX ix_rec_clean_operation_new_bag_fk (
        tenant_id,
        organization_id,
        new_bag_id
    ),
    INDEX ix_rec_clean_operation_pending_session_fk (
        tenant_id,
        organization_id,
        deployment_id,
        port_id,
        pending_delivery_result_session_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE rec_clean_record (
    id BIGINT NOT NULL AUTO_INCREMENT,
    clean_record_no VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    visibility_sequence_no BIGINT NOT NULL,
    clean_operation_id BIGINT NOT NULL,
    physical_result_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    port_id BIGINT NOT NULL,
    cleaner_organization_user_id BIGINT NOT NULL,
    clean_config_version_id BIGINT NOT NULL,
    clean_config_version_no BIGINT NOT NULL,
    old_bag_binding_state VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    old_baseline_state VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    old_bag_id BIGINT NULL,
    old_bag_code_snapshot VARCHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    new_bag_id BIGINT NOT NULL,
    new_bag_code_snapshot VARCHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    pre_unlock_weight_status VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    pre_unlock_weight_g BIGINT NULL,
    old_baseline_weight_g BIGINT NULL,
    device_removed_net_weight_status VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    device_removed_net_weight_g BIGINT NULL,
    recalculated_removed_net_weight_status VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    recalculated_removed_net_weight_g BIGINT NULL,
    final_total_weight_status VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    final_total_weight_g BIGINT NULL,
    record_class VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    review_status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    review_revision_no BIGINT NOT NULL DEFAULT 0,
    review_revision_id BIGINT NULL,
    final_recognized_net_weight_kg DECIMAL(15, 2) NULL,
    device_occurred_at DATETIME(3) NOT NULL,
    completed_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_rec_clean_record_no UNIQUE (clean_record_no),
    CONSTRAINT uq_rec_clean_record_visibility
        UNIQUE (tenant_id, organization_id, visibility_sequence_no),
    CONSTRAINT uq_rec_clean_record_operation UNIQUE (clean_operation_id),
    CONSTRAINT uq_rec_clean_record_result UNIQUE (physical_result_id),
    CONSTRAINT uq_rec_clean_record_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_rec_clean_record_operation_ref
        UNIQUE (tenant_id, organization_id, clean_operation_id, id),
    CONSTRAINT uq_rec_clean_record_port_id
        UNIQUE (tenant_id, organization_id, port_id, id),
    CONSTRAINT uq_rec_clean_record_detection_ref
        UNIQUE (tenant_id, organization_id, port_id, id, new_bag_id),
    CONSTRAINT ck_rec_clean_record_no CHECK (
        BINARY clean_record_no = BINARY TRIM(clean_record_no)
        AND CHAR_LENGTH(clean_record_no) BETWEEN 8 AND 64
    ),
    CONSTRAINT ck_rec_clean_record_values CHECK (
        visibility_sequence_no BETWEEN 1 AND 9007199254740991
        AND record_class IN ('NORMAL', 'SYSTEM_ANOMALY')
        AND new_bag_code_snapshot REGEXP '^[A-Za-z0-9_-]{8,64}$'
    ),
    CONSTRAINT ck_rec_clean_record_old_bag CHECK (
        (
            old_bag_binding_state = 'BOUND'
            AND old_bag_id IS NOT NULL
            AND old_bag_code_snapshot IS NOT NULL
            AND old_bag_code_snapshot REGEXP '^[A-Za-z0-9_-]{8,64}$'
        )
        OR
        (
            old_bag_binding_state = 'MISSING'
            AND old_bag_id IS NULL
            AND old_bag_code_snapshot IS NULL
        )
    ),
    CONSTRAINT ck_rec_clean_record_baseline CHECK (
        old_baseline_state IN ('TRUSTED', 'UNTRUSTED', 'MISSING')
        AND (
            old_baseline_state <> 'TRUSTED'
            OR (
                old_bag_binding_state = 'BOUND'
                AND old_baseline_weight_g IS NOT NULL
                AND old_baseline_weight_g >= 0
            )
        )
        AND (
            old_baseline_state <> 'MISSING'
            OR old_baseline_weight_g IS NULL
        )
    ),
    CONSTRAINT ck_rec_clean_record_weight_states CHECK (
        pre_unlock_weight_status IN ('RELIABLE', 'FAILED')
        AND device_removed_net_weight_status IN ('RELIABLE', 'FAILED')
        AND recalculated_removed_net_weight_status IN (
            'RELIABLE',
            'UNAVAILABLE',
            'INVALID'
        )
        AND final_total_weight_status IN ('RELIABLE', 'INVALID', 'FAILED')
        AND (
            (pre_unlock_weight_status = 'RELIABLE')
            =
            (pre_unlock_weight_g IS NOT NULL)
        )
        AND (
            (device_removed_net_weight_status = 'RELIABLE')
            =
            (device_removed_net_weight_g IS NOT NULL)
        )
        AND (
            (
                recalculated_removed_net_weight_status IN (
                    'RELIABLE',
                    'INVALID'
                )
            )
            =
            (recalculated_removed_net_weight_g IS NOT NULL)
        )
        AND (
            (final_total_weight_status IN ('RELIABLE', 'INVALID'))
            =
            (final_total_weight_g IS NOT NULL)
        )
    ),
    CONSTRAINT ck_rec_clean_record_review CHECK (
        (
            review_status = 'PENDING'
            AND review_revision_no = 0
            AND review_revision_id IS NULL
            AND final_recognized_net_weight_kg IS NULL
        )
        OR
        (
            review_status = 'APPROVED'
            AND review_revision_no = 1
            AND review_revision_id IS NOT NULL
            AND final_recognized_net_weight_kg IS NOT NULL
        )
    ),
    CONSTRAINT ck_rec_clean_record_times CHECK (
        completed_at >= device_occurred_at
        AND created_at >= completed_at
        AND updated_at >= created_at
    ),
    CONSTRAINT fk_rec_clean_record_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_clean_record_operation
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            port_id,
            clean_operation_id
        )
        REFERENCES rec_clean_operation (
            tenant_id,
            organization_id,
            deployment_id,
            port_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_clean_record_result_scope
        FOREIGN KEY (tenant_id, organization_id, physical_result_id)
        REFERENCES dev_physical_result (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_clean_record_cleaner
        FOREIGN KEY (
            tenant_id,
            organization_id,
            cleaner_organization_user_id
        )
        REFERENCES iam_organization_user (
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_clean_record_config
        FOREIGN KEY (
            tenant_id,
            organization_id,
            clean_config_version_id,
            clean_config_version_no
        )
        REFERENCES rec_organization_clean_config (
            tenant_id,
            organization_id,
            id,
            version_no
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_clean_record_old_bag
        FOREIGN KEY (tenant_id, organization_id, old_bag_id)
        REFERENCES rec_bag (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_clean_record_new_bag
        FOREIGN KEY (tenant_id, organization_id, new_bag_id)
        REFERENCES rec_bag (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_clean_record_review (
        tenant_id,
        organization_id,
        review_status,
        completed_at,
        clean_record_no
    ),
    INDEX ix_rec_clean_record_cleaner (
        tenant_id,
        organization_id,
        cleaner_organization_user_id,
        completed_at,
        clean_record_no
    ),
    INDEX ix_rec_clean_record_port (
        tenant_id,
        organization_id,
        deployment_id,
        port_id,
        completed_at,
        clean_record_no
    ),
    INDEX ix_rec_clean_record_old_bag (
        tenant_id,
        organization_id,
        old_bag_id,
        completed_at,
        clean_record_no
    ),
    INDEX ix_rec_clean_record_new_bag (
        tenant_id,
        organization_id,
        new_bag_id,
        completed_at,
        clean_record_no
    ),
    INDEX ix_rec_clean_record_result_fk (
        tenant_id,
        organization_id,
        physical_result_id
    ),
    INDEX ix_rec_clean_record_operation_fk (
        tenant_id,
        organization_id,
        deployment_id,
        port_id,
        clean_operation_id
    ),
    INDEX ix_rec_clean_record_config_fk (
        tenant_id,
        organization_id,
        clean_config_version_id,
        clean_config_version_no
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE rec_clean_anomaly (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    clean_record_id BIGINT NOT NULL,
    anomaly_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    diagnostic_json JSON NULL,
    detected_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_rec_clean_anomaly_code
        UNIQUE (clean_record_id, anomaly_code),
    CONSTRAINT uq_rec_clean_anomaly_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT ck_rec_clean_anomaly_code CHECK (
        BINARY anomaly_code = BINARY TRIM(anomaly_code)
        AND CHAR_LENGTH(anomaly_code) BETWEEN 1 AND 64
    ),
    CONSTRAINT ck_rec_clean_anomaly_times
        CHECK (detected_at >= created_at),
    CONSTRAINT fk_rec_clean_anomaly_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_clean_anomaly_record
        FOREIGN KEY (tenant_id, organization_id, clean_record_id)
        REFERENCES rec_clean_record (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_clean_anomaly_org_time (
        tenant_id,
        organization_id,
        anomaly_code,
        detected_at,
        id
    ),
    INDEX ix_rec_clean_anomaly_record_fk (
        tenant_id,
        organization_id,
        clean_record_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE rec_clean_revision (
    id BIGINT NOT NULL AUTO_INCREMENT,
    revision_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    clean_record_id BIGINT NOT NULL,
    revision_no BIGINT NOT NULL DEFAULT 1,
    decision_type VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    before_final_net_weight_kg DECIMAL(15, 2) NULL,
    after_final_net_weight_kg DECIMAL(15, 2) NOT NULL,
    reviewer_kind VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    platform_admin_id BIGINT NULL,
    staff_account_id BIGINT NULL,
    note VARCHAR(500) NULL,
    request_sha256 BINARY(32) NOT NULL,
    reviewed_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_rec_clean_revision_uid UNIQUE (revision_uid),
    CONSTRAINT uq_rec_clean_revision_record UNIQUE (clean_record_id),
    CONSTRAINT uq_rec_clean_revision_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_rec_clean_revision_current_ref UNIQUE (
        tenant_id,
        organization_id,
        clean_record_id,
        id,
        revision_no,
        after_final_net_weight_kg
    ),
    CONSTRAINT ck_rec_clean_revision_uid_v4 CHECK (
        revision_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_rec_clean_revision_initial CHECK (
        revision_no = 1
        AND before_final_net_weight_kg IS NULL
        AND decision_type IN ('ORIGINAL_APPROVED', 'MODIFIED_APPROVED')
    ),
    CONSTRAINT ck_rec_clean_revision_actor CHECK (
        (
            reviewer_kind = 'PLATFORM_ADMIN'
            AND platform_admin_id IS NOT NULL
            AND staff_account_id IS NULL
        )
        OR
        (
            reviewer_kind = 'STAFF'
            AND platform_admin_id IS NULL
            AND staff_account_id IS NOT NULL
        )
    ),
    CONSTRAINT ck_rec_clean_revision_note CHECK (
        note IS NULL OR CHAR_LENGTH(TRIM(note)) > 0
    ),
    CONSTRAINT ck_rec_clean_revision_times
        CHECK (reviewed_at >= created_at),
    CONSTRAINT fk_rec_clean_revision_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_clean_revision_record
        FOREIGN KEY (tenant_id, organization_id, clean_record_id)
        REFERENCES rec_clean_record (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_clean_revision_platform
        FOREIGN KEY (platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_clean_revision_staff
        FOREIGN KEY (tenant_id, staff_account_id)
        REFERENCES iam_staff_account (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_clean_revision_record_fk (
        tenant_id,
        organization_id,
        clean_record_id
    ),
    INDEX ix_rec_clean_revision_platform_fk (platform_admin_id),
    INDEX ix_rec_clean_revision_staff_fk (tenant_id, staff_account_id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE rec_clean_record
    ADD CONSTRAINT fk_rec_clean_record_review_revision
        FOREIGN KEY (
            tenant_id,
            organization_id,
            id,
            review_revision_id,
            review_revision_no,
            final_recognized_net_weight_kg
        )
        REFERENCES rec_clean_revision (
            tenant_id,
            organization_id,
            clean_record_id,
            id,
            revision_no,
            after_final_net_weight_kg
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_rec_clean_record_review_revision_fk (
        tenant_id,
        organization_id,
        id,
        review_revision_id,
        review_revision_no,
        final_recognized_net_weight_kg
    );

ALTER TABLE rec_clean_operation
    ADD CONSTRAINT fk_rec_clean_operation_completion
        FOREIGN KEY (
            tenant_id,
            organization_id,
            id,
            completion_record_id
        )
        REFERENCES rec_clean_record (
            tenant_id,
            organization_id,
            clean_operation_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_rec_clean_operation_completion_fk (
        tenant_id,
        organization_id,
        id,
        completion_record_id
    );

CREATE TABLE rec_clean_photo (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    clean_operation_id BIGINT NOT NULL,
    position VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    photo_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    status VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    object_url VARCHAR(1500) CHARACTER SET ascii COLLATE ascii_bin NULL,
    sha256 BINARY(32) NULL,
    size_bytes BIGINT NULL,
    captured_at DATETIME(3) NULL,
    linked_at DATETIME(3) NULL,
    missing_reason VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NULL,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_rec_clean_photo_position
        UNIQUE (clean_operation_id, position),
    CONSTRAINT uq_rec_clean_photo_uid UNIQUE (photo_uid),
    CONSTRAINT uq_rec_clean_photo_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT ck_rec_clean_photo_position CHECK (
        position IN (
            'FIRST_OPEN_INNER',
            'FIRST_OPEN_OUTER',
            'FINAL_CLOSE_INNER',
            'FINAL_CLOSE_OUTER'
        )
    ),
    CONSTRAINT ck_rec_clean_photo_uid CHECK (
        photo_uid IS NULL
        OR photo_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_rec_clean_photo_state CHECK (
        (
            status = 'UPLOAD_PENDING'
            AND object_url IS NULL
            AND sha256 IS NULL
            AND size_bytes IS NULL
            AND linked_at IS NULL
            AND missing_reason IS NULL
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
            AND sha256 IS NULL
            AND size_bytes IS NULL
            AND linked_at IS NOT NULL
            AND missing_reason IS NOT NULL
            AND CHAR_LENGTH(TRIM(missing_reason)) > 0
        )
    ),
    CONSTRAINT ck_rec_clean_photo_url CHECK (
        object_url IS NULL
        OR (
            object_url REGEXP '^https://[^/?#]+/ecobin/.+\\.jpg$'
            AND INSTR(object_url, '?') = 0
            AND INSTR(object_url, '#') = 0
        )
    ),
    CONSTRAINT ck_rec_clean_photo_times CHECK (
        updated_at >= created_at
        AND (captured_at IS NULL OR captured_at <= updated_at)
        AND (linked_at IS NULL OR linked_at >= created_at)
    ),
    CONSTRAINT fk_rec_clean_photo_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_clean_photo_operation
        FOREIGN KEY (tenant_id, organization_id, clean_operation_id)
        REFERENCES rec_clean_operation (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_clean_photo_pending (
        tenant_id,
        organization_id,
        status,
        updated_at,
        id
    ),
    INDEX ix_rec_clean_photo_operation_fk (
        tenant_id,
        organization_id,
        clean_operation_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE rec_clean_operation
    ADD CONSTRAINT uq_rec_clean_operation_reserved_bag_ref
        UNIQUE (tenant_id, organization_id, id, new_bag_id);

CREATE TABLE rec_bag_current_occupancy (
    bag_id BIGINT NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    occupancy_type VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    port_id BIGINT NULL,
    clean_operation_id BIGINT NULL,
    acquired_at DATETIME(3) NOT NULL,
    PRIMARY KEY (bag_id),
    CONSTRAINT uq_rec_bag_occupancy_port UNIQUE (port_id),
    CONSTRAINT uq_rec_bag_occupancy_operation UNIQUE (clean_operation_id),
    CONSTRAINT uq_rec_bag_occupancy_scope
        UNIQUE (tenant_id, organization_id, bag_id),
    CONSTRAINT ck_rec_bag_occupancy_target CHECK (
        (
            occupancy_type = 'PORT_BOUND'
            AND port_id IS NOT NULL
            AND clean_operation_id IS NULL
        )
        OR
        (
            occupancy_type = 'CLEAN_RESERVED'
            AND port_id IS NULL
            AND clean_operation_id IS NOT NULL
        )
    ),
    CONSTRAINT fk_rec_bag_occupancy_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_bag_occupancy_bag
        FOREIGN KEY (tenant_id, organization_id, bag_id)
        REFERENCES rec_bag (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_bag_occupancy_port
        FOREIGN KEY (tenant_id, organization_id, port_id)
        REFERENCES dev_port (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_bag_occupancy_reservation
        FOREIGN KEY (
            tenant_id,
            organization_id,
            clean_operation_id,
            bag_id
        )
        REFERENCES rec_clean_operation (
            tenant_id,
            organization_id,
            id,
            new_bag_id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_bag_occupancy_org_fk (
        tenant_id,
        organization_id
    ),
    INDEX ix_rec_bag_occupancy_bag_fk (
        tenant_id,
        organization_id,
        bag_id
    ),
    INDEX ix_rec_bag_occupancy_port_fk (
        tenant_id,
        organization_id,
        port_id
    ),
    INDEX ix_rec_bag_occupancy_reservation_fk (
        tenant_id,
        organization_id,
        clean_operation_id,
        bag_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE rec_bag_occupancy_event (
    id BIGINT NOT NULL AUTO_INCREMENT,
    event_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    bag_id BIGINT NOT NULL,
    port_id BIGINT NOT NULL,
    clean_operation_id BIGINT NULL,
    event_type VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    occurred_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_rec_bag_event_uid UNIQUE (event_uid),
    CONSTRAINT uq_rec_bag_event_clean_type
        UNIQUE (clean_operation_id, event_type),
    CONSTRAINT uq_rec_bag_event_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT ck_rec_bag_event_uid_v4 CHECK (
        event_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_rec_bag_event_source CHECK (
        (
            event_type = 'INITIAL_INSTALLED'
            AND clean_operation_id IS NULL
        )
        OR
        (
            event_type IN (
                'RESERVED_FOR_CLEAN',
                'RESERVATION_RELEASED',
                'REMOVED_BY_CLEAN',
                'INSTALLED_BY_CLEAN'
            )
            AND clean_operation_id IS NOT NULL
        )
    ),
    CONSTRAINT ck_rec_bag_event_times CHECK (occurred_at >= created_at),
    CONSTRAINT fk_rec_bag_event_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_bag_event_bag
        FOREIGN KEY (tenant_id, organization_id, bag_id)
        REFERENCES rec_bag (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_bag_event_port
        FOREIGN KEY (tenant_id, organization_id, port_id)
        REFERENCES dev_port (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_bag_event_operation
        FOREIGN KEY (tenant_id, organization_id, clean_operation_id)
        REFERENCES rec_clean_operation (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_bag_event_bag_history (
        tenant_id,
        organization_id,
        bag_id,
        occurred_at,
        event_uid
    ),
    INDEX ix_rec_bag_event_port_history (
        tenant_id,
        organization_id,
        port_id,
        occurred_at,
        event_uid
    ),
    INDEX ix_rec_bag_event_operation_fk (
        tenant_id,
        organization_id,
        clean_operation_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE rec_port_baseline_measurement (
    id BIGINT NOT NULL AUTO_INCREMENT,
    measurement_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    port_id BIGINT NOT NULL,
    bag_id BIGINT NOT NULL,
    device_config_version_id BIGINT NOT NULL,
    port_config_snapshot_id BIGINT NOT NULL,
    capacity_lock_version_snapshot BIGINT NOT NULL,
    fullness_rule_fingerprint BINARY(32) NOT NULL,
    initiator_kind VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    platform_admin_id BIGINT NULL,
    staff_account_id BIGINT NULL,
    status VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    physical_result_id BIGINT NULL,
    stable_total_weight_g BIGINT NULL,
    fault_code VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NULL,
    result_baseline_id BIGINT NULL,
    started_at DATETIME(3) NOT NULL,
    completed_at DATETIME(3) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    active_port_id BIGINT
        GENERATED ALWAYS AS (
            CASE WHEN status = 'PENDING' THEN port_id ELSE NULL END
        ) STORED,
    PRIMARY KEY (id),
    CONSTRAINT uq_rec_baseline_measurement_uid UNIQUE (measurement_uid),
    CONSTRAINT uq_rec_baseline_measurement_result UNIQUE (physical_result_id),
    CONSTRAINT uq_rec_baseline_measurement_active_port UNIQUE (active_port_id),
    CONSTRAINT uq_rec_baseline_measurement_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_rec_baseline_measurement_port_id
        UNIQUE (tenant_id, organization_id, port_id, id),
    CONSTRAINT ck_rec_baseline_measurement_uid_v4 CHECK (
        measurement_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_rec_baseline_measurement_actor CHECK (
        (
            initiator_kind = 'PLATFORM_ADMIN'
            AND platform_admin_id IS NOT NULL
            AND staff_account_id IS NULL
        )
        OR
        (
            initiator_kind = 'STAFF'
            AND platform_admin_id IS NULL
            AND staff_account_id IS NOT NULL
        )
    ),
    CONSTRAINT ck_rec_baseline_measurement_state CHECK (
        (
            status = 'PENDING'
            AND physical_result_id IS NULL
            AND stable_total_weight_g IS NULL
            AND fault_code IS NULL
            AND result_baseline_id IS NULL
            AND completed_at IS NULL
        )
        OR
        (
            status = 'COMPLETED'
            AND physical_result_id IS NOT NULL
            AND stable_total_weight_g IS NOT NULL
            AND stable_total_weight_g >= 0
            AND fault_code IS NULL
            AND result_baseline_id IS NOT NULL
            AND completed_at IS NOT NULL
        )
        OR
        (
            status = 'FAILED'
            AND physical_result_id IS NOT NULL
            AND stable_total_weight_g IS NULL
            AND fault_code IS NOT NULL
            AND CHAR_LENGTH(TRIM(fault_code)) > 0
            AND result_baseline_id IS NULL
            AND completed_at IS NOT NULL
        )
        OR
        (
            status = 'STALE_IGNORED'
            AND physical_result_id IS NOT NULL
            AND result_baseline_id IS NULL
            AND completed_at IS NOT NULL
        )
    ),
    CONSTRAINT ck_rec_baseline_measurement_values CHECK (
        capacity_lock_version_snapshot >= 0
        AND lock_version >= 0
    ),
    CONSTRAINT ck_rec_baseline_measurement_times CHECK (
        started_at >= created_at
        AND updated_at >= created_at
        AND (completed_at IS NULL OR completed_at >= started_at)
    ),
    CONSTRAINT fk_rec_baseline_measurement_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_baseline_measurement_port
        FOREIGN KEY (tenant_id, organization_id, deployment_id, port_id)
        REFERENCES dev_port (tenant_id, organization_id, deployment_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_baseline_measurement_bag
        FOREIGN KEY (tenant_id, organization_id, bag_id)
        REFERENCES rec_bag (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_baseline_measurement_device_config
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            device_config_version_id
        )
        REFERENCES dev_config_version (
            tenant_id,
            organization_id,
            deployment_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_baseline_measurement_port_config
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            device_config_version_id,
            port_id,
            port_config_snapshot_id
        )
        REFERENCES dev_port_config_snapshot (
            tenant_id,
            organization_id,
            deployment_id,
            config_version_id,
            port_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_baseline_measurement_platform
        FOREIGN KEY (platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_baseline_measurement_staff
        FOREIGN KEY (tenant_id, staff_account_id)
        REFERENCES iam_staff_account (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_baseline_measurement_result_scope
        FOREIGN KEY (tenant_id, organization_id, physical_result_id)
        REFERENCES dev_physical_result (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_baseline_measurement_pending (
        status,
        started_at,
        id
    ),
    INDEX ix_rec_baseline_measurement_port_history (
        tenant_id,
        organization_id,
        port_id,
        started_at,
        measurement_uid
    ),
    INDEX ix_rec_baseline_measurement_port_fk (
        tenant_id,
        organization_id,
        deployment_id,
        port_id
    ),
    INDEX ix_rec_baseline_measurement_bag_fk (
        tenant_id,
        organization_id,
        bag_id
    ),
    INDEX ix_rec_baseline_measurement_device_config_fk (
        tenant_id,
        organization_id,
        deployment_id,
        device_config_version_id
    ),
    INDEX ix_rec_baseline_measurement_port_config_fk (
        tenant_id,
        organization_id,
        deployment_id,
        device_config_version_id,
        port_id,
        port_config_snapshot_id
    ),
    INDEX ix_rec_baseline_measurement_platform_fk (platform_admin_id),
    INDEX ix_rec_baseline_measurement_staff_fk (
        tenant_id,
        staff_account_id
    ),
    INDEX ix_rec_baseline_measurement_result_fk (
        tenant_id,
        organization_id,
        physical_result_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE rec_port_weight_baseline (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    port_id BIGINT NOT NULL,
    bag_id BIGINT NOT NULL,
    version_no BIGINT NOT NULL,
    source_type VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_bag_event_id BIGINT NULL,
    source_physical_result_id BIGINT NULL,
    source_clean_record_id BIGINT NULL,
    source_measurement_id BIGINT NULL,
    baseline_weight_g BIGINT NOT NULL,
    established_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_rec_weight_baseline_port_version
        UNIQUE (port_id, version_no),
    CONSTRAINT uq_rec_weight_baseline_bag_event
        UNIQUE (source_bag_event_id),
    CONSTRAINT uq_rec_weight_baseline_clean_record
        UNIQUE (source_clean_record_id),
    CONSTRAINT uq_rec_weight_baseline_measurement
        UNIQUE (source_measurement_id),
    CONSTRAINT uq_rec_weight_baseline_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_rec_weight_baseline_port_id
        UNIQUE (
            tenant_id,
            organization_id,
            port_id,
            id
        ),
    CONSTRAINT uq_rec_weight_baseline_capacity_ref UNIQUE (
        tenant_id,
        organization_id,
        port_id,
        id,
        baseline_weight_g
    ),
    CONSTRAINT uq_rec_weight_baseline_snapshot_ref UNIQUE (
        tenant_id,
        organization_id,
        port_id,
        id,
        bag_id,
        baseline_weight_g
    ),
    CONSTRAINT ck_rec_weight_baseline_values CHECK (
        version_no BETWEEN 1 AND 9007199254740991
        AND baseline_weight_g >= 0
    ),
    CONSTRAINT ck_rec_weight_baseline_source CHECK (
        (
            source_type = 'INITIAL_BINDING'
            AND source_bag_event_id IS NOT NULL
            AND source_physical_result_id IS NULL
            AND source_clean_record_id IS NULL
            AND source_measurement_id IS NULL
        )
        OR
        (
            source_type = 'CLEAN_COMPLETE'
            AND source_bag_event_id IS NOT NULL
            AND source_physical_result_id IS NOT NULL
            AND source_clean_record_id IS NOT NULL
            AND source_measurement_id IS NULL
        )
        OR
        (
            source_type = 'MANUAL_REMEASUREMENT'
            AND source_bag_event_id IS NULL
            AND source_physical_result_id IS NOT NULL
            AND source_clean_record_id IS NULL
            AND source_measurement_id IS NOT NULL
        )
    ),
    CONSTRAINT ck_rec_weight_baseline_times CHECK (
        established_at >= created_at
    ),
    CONSTRAINT fk_rec_weight_baseline_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_weight_baseline_port
        FOREIGN KEY (tenant_id, organization_id, port_id)
        REFERENCES dev_port (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_weight_baseline_bag
        FOREIGN KEY (tenant_id, organization_id, bag_id)
        REFERENCES rec_bag (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_weight_baseline_bag_event
        FOREIGN KEY (tenant_id, organization_id, source_bag_event_id)
        REFERENCES rec_bag_occupancy_event (
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_weight_baseline_result
        FOREIGN KEY (tenant_id, organization_id, source_physical_result_id)
        REFERENCES dev_physical_result (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_weight_baseline_clean_record
        FOREIGN KEY (tenant_id, organization_id, source_clean_record_id)
        REFERENCES rec_clean_record (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_weight_baseline_measurement
        FOREIGN KEY (tenant_id, organization_id, source_measurement_id)
        REFERENCES rec_port_baseline_measurement (
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_weight_baseline_port_version (
        tenant_id,
        organization_id,
        port_id,
        version_no DESC
    ),
    INDEX ix_rec_weight_baseline_bag_fk (
        tenant_id,
        organization_id,
        bag_id
    ),
    INDEX ix_rec_weight_baseline_bag_event_fk (
        tenant_id,
        organization_id,
        source_bag_event_id
    ),
    INDEX ix_rec_weight_baseline_result_fk (
        tenant_id,
        organization_id,
        source_physical_result_id
    ),
    INDEX ix_rec_weight_baseline_clean_record_fk (
        tenant_id,
        organization_id,
        source_clean_record_id
    ),
    INDEX ix_rec_weight_baseline_measurement_fk (
        tenant_id,
        organization_id,
        source_measurement_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE rec_port_baseline_measurement
    ADD CONSTRAINT fk_rec_baseline_measurement_result_baseline
        FOREIGN KEY (
            tenant_id,
            organization_id,
            port_id,
            result_baseline_id,
            bag_id,
            stable_total_weight_g
        )
        REFERENCES rec_port_weight_baseline (
            tenant_id,
            organization_id,
            port_id,
            id,
            bag_id,
            baseline_weight_g
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_rec_baseline_measurement_result_baseline_fk (
        tenant_id,
        organization_id,
        port_id,
        result_baseline_id,
        bag_id,
        stable_total_weight_g
    );

ALTER TABLE rec_clean_operation
    ADD CONSTRAINT fk_rec_clean_operation_old_baseline_identity
        FOREIGN KEY (
            tenant_id,
            organization_id,
            old_baseline_id
        )
        REFERENCES rec_port_weight_baseline (
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_rec_clean_operation_old_baseline
        FOREIGN KEY (
            tenant_id,
            organization_id,
            port_id,
            old_baseline_id,
            old_bag_id,
            old_baseline_weight_g
        )
        REFERENCES rec_port_weight_baseline (
            tenant_id,
            organization_id,
            port_id,
            id,
            bag_id,
            baseline_weight_g
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_rec_clean_operation_old_baseline_fk (
        tenant_id,
        organization_id,
        port_id,
        old_baseline_id,
        old_bag_id,
        old_baseline_weight_g
    ),
    ADD INDEX ix_rec_clean_operation_old_baseline_identity_fk (
        tenant_id,
        organization_id,
        old_baseline_id
    );

CREATE TABLE rec_port_capacity_state (
    port_id BIGINT NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    baseline_state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    current_baseline_id BIGINT NULL,
    current_baseline_weight_g BIGINT NULL,
    latest_stable_total_weight_g BIGINT NULL,
    raw_net_weight_g BIGINT NULL,
    displayed_fullness_percent DECIMAL(9, 2) NULL,
    detection_gate VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    current_detection_id BIGINT NULL,
    current_rule_fingerprint BINARY(32) NULL,
    confirmed_fullness_state VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    last_detection_id BIGINT NULL,
    current_fullness_event_id BIGINT NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (port_id),
    CONSTRAINT uq_rec_capacity_scope_port
        UNIQUE (tenant_id, organization_id, port_id),
    CONSTRAINT ck_rec_capacity_baseline CHECK (
        (
            baseline_state = 'VALID'
            AND current_baseline_id IS NOT NULL
            AND current_baseline_weight_g IS NOT NULL
            AND current_baseline_weight_g >= 0
        )
        OR
        (
            baseline_state IN ('UNINITIALIZED', 'INVALID')
            AND current_baseline_id IS NULL
            AND current_baseline_weight_g IS NULL
        )
    ),
    CONSTRAINT ck_rec_capacity_gate CHECK (
        detection_gate IN (
            'UNKNOWN',
            'PENDING',
            'IN_PROGRESS',
            'READY',
            'FAILED'
        )
        AND confirmed_fullness_state IN ('UNKNOWN', 'NOT_FULL', 'FULL')
        AND (
            displayed_fullness_percent IS NULL
            OR displayed_fullness_percent >= 0
        )
        AND (
            (
                baseline_state = 'VALID'
                AND (
                    (
                        raw_net_weight_g IS NULL
                        AND displayed_fullness_percent IS NULL
                    )
                    OR
                    (
                        latest_stable_total_weight_g IS NOT NULL
                        AND raw_net_weight_g IS NOT NULL
                        AND displayed_fullness_percent IS NOT NULL
                    )
                )
            )
            OR
            (
                baseline_state IN ('UNINITIALIZED', 'INVALID')
                AND raw_net_weight_g IS NULL
                AND displayed_fullness_percent IS NULL
            )
        )
        AND (
            detection_gate IN ('PENDING', 'IN_PROGRESS')
            =
            (current_detection_id IS NOT NULL)
        )
        AND (
            detection_gate <> 'READY'
            OR confirmed_fullness_state IN ('NOT_FULL', 'FULL')
        )
        AND lock_version >= 0
    ),
    CONSTRAINT fk_rec_capacity_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_capacity_port
        FOREIGN KEY (tenant_id, organization_id, deployment_id, port_id)
        REFERENCES dev_port (tenant_id, organization_id, deployment_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_capacity_current_baseline
        FOREIGN KEY (
            tenant_id,
            organization_id,
            port_id,
            current_baseline_id,
            current_baseline_weight_g
        )
        REFERENCES rec_port_weight_baseline (
            tenant_id,
            organization_id,
            port_id,
            id,
            baseline_weight_g
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_capacity_gate (
        tenant_id,
        organization_id,
        detection_gate,
        confirmed_fullness_state,
        port_id
    ),
    INDEX ix_rec_capacity_port_fk (
        tenant_id,
        organization_id,
        deployment_id,
        port_id
    ),
    INDEX ix_rec_capacity_baseline_fk (
        tenant_id,
        organization_id,
        port_id,
        current_baseline_id,
        current_baseline_weight_g
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE rec_fullness_detection (
    id BIGINT NOT NULL AUTO_INCREMENT,
    detection_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    port_id BIGINT NOT NULL,
    trigger_type VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    delivery_order_id BIGINT NULL,
    clean_record_id BIGINT NULL,
    initiator_kind VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NULL,
    platform_admin_id BIGINT NULL,
    staff_account_id BIGINT NULL,
    bag_id BIGINT NOT NULL,
    baseline_state_snapshot VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    baseline_id_snapshot BIGINT NULL,
    baseline_weight_g_snapshot BIGINT NULL,
    device_config_version_id BIGINT NOT NULL,
    port_config_snapshot_id BIGINT NOT NULL,
    rule_fingerprint BINARY(32) NOT NULL,
    decision_mode VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    configured_full_weight_g BIGINT NOT NULL,
    settle_wait_ms BIGINT NOT NULL,
    confirmation_wait_ms BIGINT NOT NULL,
    status VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    final_result VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NULL,
    failure_code VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NULL,
    disposition VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    initial_sample_id BIGINT NULL,
    initial_sample_conclusion VARCHAR(24)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    terminal_sample_id BIGINT NULL,
    terminal_sample_conclusion VARCHAR(24)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    next_sample_at DATETIME(3) NULL,
    completed_at DATETIME(3) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    active_port_id BIGINT
        GENERATED ALWAYS AS (
            CASE
                WHEN status IN (
                    'PENDING_INITIAL_SAMPLE',
                    'WAITING_RECHECK'
                )
                THEN port_id
                ELSE NULL
            END
        ) STORED,
    PRIMARY KEY (id),
    CONSTRAINT uq_rec_fullness_detection_uid UNIQUE (detection_uid),
    CONSTRAINT uq_rec_fullness_detection_delivery UNIQUE (delivery_order_id),
    CONSTRAINT uq_rec_fullness_detection_clean UNIQUE (clean_record_id),
    CONSTRAINT uq_rec_fullness_detection_active_port UNIQUE (active_port_id),
    CONSTRAINT uq_rec_fullness_detection_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_rec_fullness_detection_port_id
        UNIQUE (tenant_id, organization_id, port_id, id),
    CONSTRAINT ck_rec_fullness_detection_uid_v4 CHECK (
        detection_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_rec_fullness_detection_source CHECK (
        (
            trigger_type = 'DELIVERY_COMPLETE'
            AND delivery_order_id IS NOT NULL
            AND clean_record_id IS NULL
            AND initiator_kind IS NULL
            AND platform_admin_id IS NULL
            AND staff_account_id IS NULL
        )
        OR
        (
            trigger_type = 'CLEAN_COMPLETE'
            AND delivery_order_id IS NULL
            AND clean_record_id IS NOT NULL
            AND initiator_kind IS NULL
            AND platform_admin_id IS NULL
            AND staff_account_id IS NULL
        )
        OR
        (
            trigger_type = 'MANUAL_RECHECK'
            AND delivery_order_id IS NULL
            AND clean_record_id IS NULL
            AND (
                (
                    initiator_kind = 'PLATFORM_ADMIN'
                    AND platform_admin_id IS NOT NULL
                    AND staff_account_id IS NULL
                )
                OR
                (
                    initiator_kind = 'STAFF'
                    AND platform_admin_id IS NULL
                    AND staff_account_id IS NOT NULL
                )
            )
        )
    ),
    CONSTRAINT ck_rec_fullness_detection_baseline CHECK (
        (
            baseline_state_snapshot = 'VALID'
            AND baseline_id_snapshot IS NOT NULL
            AND baseline_weight_g_snapshot IS NOT NULL
            AND baseline_weight_g_snapshot >= 0
        )
        OR
        (
            baseline_state_snapshot IN ('INVALID', 'MISSING')
            AND baseline_id_snapshot IS NULL
            AND baseline_weight_g_snapshot IS NULL
        )
    ),
    CONSTRAINT ck_rec_fullness_detection_rules CHECK (
        decision_mode IN (
            'INFRARED_ONLY',
            'WEIGHT_ONLY',
            'INFRARED_OR_WEIGHT'
        )
        AND configured_full_weight_g > 0
        AND settle_wait_ms >= 0
        AND confirmation_wait_ms >= 0
        AND lock_version >= 0
    ),
    CONSTRAINT ck_rec_fullness_detection_state CHECK (
        (
            (
                status = 'PENDING_INITIAL_SAMPLE'
                AND final_result IS NULL
                AND failure_code IS NULL
                AND disposition = 'PENDING'
                AND initial_sample_id IS NULL
                AND initial_sample_conclusion IS NULL
                AND terminal_sample_id IS NULL
                AND terminal_sample_conclusion IS NULL
                AND completed_at IS NULL
            )
            OR
            (
                status = 'WAITING_RECHECK'
                AND final_result IS NULL
                AND failure_code IS NULL
                AND disposition = 'PENDING'
                AND initial_sample_id IS NOT NULL
                AND initial_sample_conclusion = 'FULL'
                AND terminal_sample_id IS NULL
                AND terminal_sample_conclusion IS NULL
                AND next_sample_at IS NOT NULL
                AND completed_at IS NULL
            )
            OR
            (
                status = 'COMPLETED'
                AND final_result IN ('NOT_FULL', 'FULL')
                AND failure_code IS NULL
                AND disposition IN ('APPLIED', 'STALE_IGNORED')
                AND terminal_sample_id IS NOT NULL
                AND terminal_sample_conclusion = final_result
                AND completed_at IS NOT NULL
            )
            OR
            (
                status = 'FAILED'
                AND final_result = 'SOURCE_FAILED'
                AND failure_code IS NOT NULL
                AND CHAR_LENGTH(TRIM(failure_code)) > 0
                AND disposition IN ('APPLIED', 'STALE_IGNORED')
                AND completed_at IS NOT NULL
                AND (
                    (
                        terminal_sample_id IS NOT NULL
                        AND terminal_sample_conclusion = 'SOURCE_FAILED'
                    )
                    OR (
                        trigger_type = 'CLEAN_COMPLETE'
                        AND baseline_state_snapshot IN ('INVALID', 'MISSING')
                        AND failure_code = 'WEIGHT_BASELINE_UNAVAILABLE'
                        AND initial_sample_id IS NULL
                        AND initial_sample_conclusion IS NULL
                        AND terminal_sample_id IS NULL
                        AND terminal_sample_conclusion IS NULL
                    )
                )
            )
        )
        AND (
            (initial_sample_id IS NULL)
            =
            (initial_sample_conclusion IS NULL)
        )
        AND (
            (terminal_sample_id IS NULL)
            =
            (terminal_sample_conclusion IS NULL)
        )
    ),
    CONSTRAINT ck_rec_fullness_detection_times CHECK (
        updated_at >= created_at
        AND (next_sample_at IS NULL OR next_sample_at >= created_at)
        AND (completed_at IS NULL OR completed_at >= created_at)
    ),
    CONSTRAINT fk_rec_fullness_detection_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_fullness_detection_port
        FOREIGN KEY (tenant_id, organization_id, deployment_id, port_id)
        REFERENCES dev_port (tenant_id, organization_id, deployment_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_fullness_detection_delivery
        FOREIGN KEY (
            tenant_id,
            organization_id,
            port_id,
            delivery_order_id,
            bag_id
        )
        REFERENCES rec_delivery_order (
            tenant_id,
            organization_id,
            port_id,
            id,
            bag_id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_fullness_detection_clean
        FOREIGN KEY (
            tenant_id,
            organization_id,
            port_id,
            clean_record_id,
            bag_id
        )
        REFERENCES rec_clean_record (
            tenant_id,
            organization_id,
            port_id,
            id,
            new_bag_id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_fullness_detection_platform
        FOREIGN KEY (platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_fullness_detection_staff
        FOREIGN KEY (tenant_id, staff_account_id)
        REFERENCES iam_staff_account (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_fullness_detection_bag
        FOREIGN KEY (tenant_id, organization_id, bag_id)
        REFERENCES rec_bag (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_fullness_detection_baseline
        FOREIGN KEY (
            tenant_id,
            organization_id,
            port_id,
            baseline_id_snapshot,
            bag_id,
            baseline_weight_g_snapshot
        )
        REFERENCES rec_port_weight_baseline (
            tenant_id,
            organization_id,
            port_id,
            id,
            bag_id,
            baseline_weight_g
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_fullness_detection_device_config
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            device_config_version_id
        )
        REFERENCES dev_config_version (
            tenant_id,
            organization_id,
            deployment_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_fullness_detection_port_config
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            device_config_version_id,
            port_id,
            port_config_snapshot_id
        )
        REFERENCES dev_port_config_snapshot (
            tenant_id,
            organization_id,
            deployment_id,
            config_version_id,
            port_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_fullness_detection_claim (
        status,
        next_sample_at,
        id
    ),
    INDEX ix_rec_fullness_detection_port_history (
        tenant_id,
        organization_id,
        port_id,
        created_at,
        detection_uid
    ),
    INDEX ix_rec_fullness_detection_port_fk (
        tenant_id,
        organization_id,
        deployment_id,
        port_id
    ),
    INDEX ix_rec_fullness_detection_delivery_fk (
        tenant_id,
        organization_id,
        port_id,
        delivery_order_id,
        bag_id
    ),
    INDEX ix_rec_fullness_detection_clean_fk (
        tenant_id,
        organization_id,
        port_id,
        clean_record_id,
        bag_id
    ),
    INDEX ix_rec_fullness_detection_platform_fk (platform_admin_id),
    INDEX ix_rec_fullness_detection_staff_fk (
        tenant_id,
        staff_account_id
    ),
    INDEX ix_rec_fullness_detection_bag_fk (
        tenant_id,
        organization_id,
        bag_id
    ),
    INDEX ix_rec_fullness_detection_baseline_fk (
        tenant_id,
        organization_id,
        port_id,
        baseline_id_snapshot,
        bag_id,
        baseline_weight_g_snapshot
    ),
    INDEX ix_rec_fullness_detection_device_config_fk (
        tenant_id,
        organization_id,
        deployment_id,
        device_config_version_id
    ),
    INDEX ix_rec_fullness_detection_port_config_fk (
        tenant_id,
        organization_id,
        deployment_id,
        device_config_version_id,
        port_id,
        port_config_snapshot_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE rec_fullness_sample (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    detection_id BIGINT NOT NULL,
    sample_role VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    physical_result_id BIGINT NOT NULL,
    infrared_status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    infrared_value VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NULL,
    infrared_full TINYINT NULL,
    weight_status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    stable_total_weight_g BIGINT NULL,
    baseline_weight_g BIGINT NULL,
    threshold_weight_g BIGINT NULL,
    raw_net_weight_g BIGINT NULL,
    displayed_fullness_percent DECIMAL(9, 2) NULL,
    weight_full TINYINT NULL,
    conclusion VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    full_reason VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NULL,
    device_occurred_at DATETIME(3) NOT NULL,
    backend_received_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_rec_fullness_sample_role
        UNIQUE (detection_id, sample_role),
    CONSTRAINT uq_rec_fullness_sample_result UNIQUE (physical_result_id),
    CONSTRAINT uq_rec_fullness_sample_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_rec_fullness_sample_detection_id
        UNIQUE (tenant_id, organization_id, detection_id, id),
    CONSTRAINT uq_rec_fullness_sample_pointer_ref UNIQUE (
        tenant_id,
        organization_id,
        detection_id,
        id,
        conclusion
    ),
    CONSTRAINT ck_rec_fullness_sample_role CHECK (
        sample_role IN ('INITIAL', 'CONFIRMATION', 'MANUAL_RECHECK')
    ),
    CONSTRAINT ck_rec_fullness_sample_infrared CHECK (
        (
            infrared_status = 'RELIABLE'
            AND infrared_value IS NOT NULL
            AND infrared_full IN (0, 1)
        )
        OR
        (
            infrared_status IN ('FAILED', 'NOT_REQUIRED')
            AND infrared_full IS NULL
        )
    ),
    CONSTRAINT ck_rec_fullness_sample_weight CHECK (
        (
            weight_status = 'RELIABLE'
            AND stable_total_weight_g IS NOT NULL
            AND baseline_weight_g IS NOT NULL
            AND threshold_weight_g > 0
            AND raw_net_weight_g IS NOT NULL
            AND displayed_fullness_percent IS NOT NULL
            AND displayed_fullness_percent >= 0
            AND weight_full IN (0, 1)
        )
        OR
        (
            weight_status IN ('FAILED', 'NOT_REQUIRED')
            AND stable_total_weight_g IS NULL
            AND baseline_weight_g IS NULL
            AND threshold_weight_g IS NULL
            AND raw_net_weight_g IS NULL
            AND displayed_fullness_percent IS NULL
            AND weight_full IS NULL
        )
    ),
    CONSTRAINT ck_rec_fullness_sample_conclusion CHECK (
        (
            conclusion = 'NOT_FULL'
            AND full_reason IS NULL
            AND (infrared_full = 0 OR infrared_full IS NULL)
            AND (weight_full = 0 OR weight_full IS NULL)
        )
        OR
        (
            conclusion = 'FULL'
            AND full_reason IN ('INFRARED', 'WEIGHT', 'BOTH')
            AND (infrared_full = 1 OR weight_full = 1)
        )
        OR
        (
            conclusion = 'SOURCE_FAILED'
            AND full_reason IS NULL
        )
    ),
    CONSTRAINT ck_rec_fullness_sample_times CHECK (
        backend_received_at >= device_occurred_at
        AND created_at >= backend_received_at
    ),
    CONSTRAINT fk_rec_fullness_sample_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_fullness_sample_detection
        FOREIGN KEY (tenant_id, organization_id, detection_id)
        REFERENCES rec_fullness_detection (
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_fullness_sample_result_scope
        FOREIGN KEY (tenant_id, organization_id, physical_result_id)
        REFERENCES dev_physical_result (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_fullness_sample_detection_fk (
        tenant_id,
        organization_id,
        detection_id
    ),
    INDEX ix_rec_fullness_sample_result_fk (
        tenant_id,
        organization_id,
        physical_result_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE rec_fullness_detection
    ADD CONSTRAINT fk_rec_fullness_detection_initial_sample
        FOREIGN KEY (
            tenant_id,
            organization_id,
            id,
            initial_sample_id,
            initial_sample_conclusion
        )
        REFERENCES rec_fullness_sample (
            tenant_id,
            organization_id,
            detection_id,
            id,
            conclusion
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_rec_fullness_detection_terminal_sample
        FOREIGN KEY (
            tenant_id,
            organization_id,
            id,
            terminal_sample_id,
            terminal_sample_conclusion
        )
        REFERENCES rec_fullness_sample (
            tenant_id,
            organization_id,
            detection_id,
            id,
            conclusion
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_rec_fullness_detection_initial_sample_fk (
        tenant_id,
        organization_id,
        id,
        initial_sample_id,
        initial_sample_conclusion
    ),
    ADD INDEX ix_rec_fullness_detection_terminal_sample_fk (
        tenant_id,
        organization_id,
        id,
        terminal_sample_id,
        terminal_sample_conclusion
    );

CREATE TABLE rec_fullness_event (
    id BIGINT NOT NULL AUTO_INCREMENT,
    event_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    port_id BIGINT NOT NULL,
    status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    first_detected_detection_id BIGINT NOT NULL,
    first_detected_at DATETIME(3) NOT NULL,
    confirmed_detection_id BIGINT NOT NULL,
    confirmed_at DATETIME(3) NOT NULL,
    current_reason VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    latest_detection_id BIGINT NOT NULL,
    latest_full_detection_id BIGINT NOT NULL,
    detection_count BIGINT NOT NULL,
    recovered_by_detection_id BIGINT NULL,
    recovered_at DATETIME(3) NULL,
    updated_at DATETIME(3) NOT NULL,
    active_port_id BIGINT
        GENERATED ALWAYS AS (
            CASE WHEN status = 'ACTIVE' THEN port_id ELSE NULL END
        ) STORED,
    PRIMARY KEY (id),
    CONSTRAINT uq_rec_fullness_event_uid UNIQUE (event_uid),
    CONSTRAINT uq_rec_fullness_event_active_port UNIQUE (active_port_id),
    CONSTRAINT uq_rec_fullness_event_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_rec_fullness_event_port_id
        UNIQUE (tenant_id, organization_id, port_id, id),
    CONSTRAINT ck_rec_fullness_event_uid_v4 CHECK (
        event_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_rec_fullness_event_state CHECK (
        current_reason IN ('INFRARED', 'WEIGHT', 'BOTH')
        AND detection_count >= 1
        AND (
            (
                status = 'ACTIVE'
                AND recovered_by_detection_id IS NULL
                AND recovered_at IS NULL
            )
            OR
            (
                status = 'RECOVERED'
                AND recovered_by_detection_id IS NOT NULL
                AND recovered_at IS NOT NULL
            )
        )
    ),
    CONSTRAINT ck_rec_fullness_event_times CHECK (
        confirmed_at >= first_detected_at
        AND updated_at >= confirmed_at
        AND (recovered_at IS NULL OR recovered_at >= confirmed_at)
    ),
    CONSTRAINT fk_rec_fullness_event_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_fullness_event_port
        FOREIGN KEY (tenant_id, organization_id, port_id)
        REFERENCES dev_port (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_fullness_event_first
        FOREIGN KEY (
            tenant_id,
            organization_id,
            port_id,
            first_detected_detection_id
        )
        REFERENCES rec_fullness_detection (
            tenant_id,
            organization_id,
            port_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_fullness_event_confirmed
        FOREIGN KEY (
            tenant_id,
            organization_id,
            port_id,
            confirmed_detection_id
        )
        REFERENCES rec_fullness_detection (
            tenant_id,
            organization_id,
            port_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_fullness_event_latest
        FOREIGN KEY (
            tenant_id,
            organization_id,
            port_id,
            latest_detection_id
        )
        REFERENCES rec_fullness_detection (
            tenant_id,
            organization_id,
            port_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_fullness_event_latest_full
        FOREIGN KEY (
            tenant_id,
            organization_id,
            port_id,
            latest_full_detection_id
        )
        REFERENCES rec_fullness_detection (
            tenant_id,
            organization_id,
            port_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_fullness_event_recovered
        FOREIGN KEY (
            tenant_id,
            organization_id,
            port_id,
            recovered_by_detection_id
        )
        REFERENCES rec_fullness_detection (
            tenant_id,
            organization_id,
            port_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_fullness_event_active (
        tenant_id,
        organization_id,
        status,
        first_detected_at,
        event_uid
    ),
    INDEX ix_rec_fullness_event_first_fk (
        tenant_id,
        organization_id,
        port_id,
        first_detected_detection_id
    ),
    INDEX ix_rec_fullness_event_confirmed_fk (
        tenant_id,
        organization_id,
        port_id,
        confirmed_detection_id
    ),
    INDEX ix_rec_fullness_event_latest_fk (
        tenant_id,
        organization_id,
        port_id,
        latest_detection_id
    ),
    INDEX ix_rec_fullness_event_latest_full_fk (
        tenant_id,
        organization_id,
        port_id,
        latest_full_detection_id
    ),
    INDEX ix_rec_fullness_event_recovered_fk (
        tenant_id,
        organization_id,
        port_id,
        recovered_by_detection_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE rec_port_capacity_state
    ADD CONSTRAINT fk_rec_capacity_current_detection
        FOREIGN KEY (
            tenant_id,
            organization_id,
            port_id,
            current_detection_id
        )
        REFERENCES rec_fullness_detection (
            tenant_id,
            organization_id,
            port_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_rec_capacity_last_detection
        FOREIGN KEY (
            tenant_id,
            organization_id,
            port_id,
            last_detection_id
        )
        REFERENCES rec_fullness_detection (
            tenant_id,
            organization_id,
            port_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_rec_capacity_fullness_event
        FOREIGN KEY (
            tenant_id,
            organization_id,
            port_id,
            current_fullness_event_id
        )
        REFERENCES rec_fullness_event (
            tenant_id,
            organization_id,
            port_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_rec_capacity_current_detection_fk (
        tenant_id,
        organization_id,
        port_id,
        current_detection_id
    ),
    ADD INDEX ix_rec_capacity_last_detection_fk (
        tenant_id,
        organization_id,
        port_id,
        last_detection_id
    ),
    ADD INDEX ix_rec_capacity_fullness_event_fk (
        tenant_id,
        organization_id,
        port_id,
        current_fullness_event_id
    );

-- V4 already reserved typed target columns for recycling. V5 adds immutable
-- candidate keys and then makes each recycling result reference prove that it
-- points to the same clean operation, fullness sample, or baseline measurement.
ALTER TABLE dev_physical_result
    ADD CONSTRAINT uq_dev_result_clean_target_ref UNIQUE (
        tenant_id,
        organization_id,
        deployment_id,
        port_id,
        id,
        clean_operation_id
    ),
    ADD CONSTRAINT uq_dev_result_fullness_target_ref UNIQUE (
        tenant_id,
        organization_id,
        id,
        fullness_sample_id
    ),
    ADD CONSTRAINT uq_dev_result_baseline_target_ref UNIQUE (
        tenant_id,
        organization_id,
        id,
        baseline_measurement_id
    );

ALTER TABLE rec_clean_record
    ADD CONSTRAINT fk_rec_clean_record_result_target
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            port_id,
            physical_result_id,
            clean_operation_id
        )
        REFERENCES dev_physical_result (
            tenant_id,
            organization_id,
            deployment_id,
            port_id,
            id,
            clean_operation_id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_rec_clean_record_result_target_fk (
        tenant_id,
        organization_id,
        deployment_id,
        port_id,
        physical_result_id,
        clean_operation_id
    );

ALTER TABLE rec_fullness_sample
    ADD CONSTRAINT fk_rec_fullness_sample_result_target
        FOREIGN KEY (
            tenant_id,
            organization_id,
            physical_result_id,
            id
        )
        REFERENCES dev_physical_result (
            tenant_id,
            organization_id,
            id,
            fullness_sample_id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_rec_fullness_sample_result_target_fk (
        tenant_id,
        organization_id,
        physical_result_id,
        id
    );

ALTER TABLE rec_port_baseline_measurement
    ADD CONSTRAINT fk_rec_baseline_measurement_result_target
        FOREIGN KEY (
            tenant_id,
            organization_id,
            physical_result_id,
            id
        )
        REFERENCES dev_physical_result (
            tenant_id,
            organization_id,
            id,
            baseline_measurement_id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_rec_baseline_measurement_result_target_fk (
        tenant_id,
        organization_id,
        physical_result_id,
        id
    );
