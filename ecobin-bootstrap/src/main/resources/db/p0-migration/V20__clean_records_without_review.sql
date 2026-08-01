-- Cleaning is a trusted-worker operation record, not a reviewable order.
-- Keep the already executed V5/V8/V10 checksums immutable and replace the
-- historical review projection with directly editable current values plus an
-- append-only change history.

DELIMITER $$

CREATE PROCEDURE p0_assert_clean_review_history_absent()
BEGIN
    IF EXISTS (
        SELECT 1
        FROM rec_clean_revision
        LIMIT 1
    ) OR EXISTS (
        SELECT 1
        FROM rec_clean_record
        WHERE review_status <> 'PENDING'
           OR review_revision_no <> 0
           OR review_revision_id IS NOT NULL
           OR final_recognized_net_weight_kg IS NOT NULL
        LIMIT 1
    ) THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT =
                'V20 refuses to discard historical clean review decisions';
    END IF;
END$$

CALL p0_assert_clean_review_history_absent()$$
DROP PROCEDURE p0_assert_clean_review_history_absent$$

DELIMITER ;

ALTER TABLE rec_clean_record
    DROP FOREIGN KEY fk_rec_clean_record_review_revision,
    DROP INDEX ix_rec_clean_record_review_revision_fk,
    DROP INDEX ix_rec_clean_record_review,
    DROP CHECK ck_rec_clean_record_review,
    ADD COLUMN effective_removed_net_weight_g BIGINT NULL
        AFTER final_total_weight_g,
    ADD COLUMN effective_weight_source
        VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
        DEFAULT 'DEVICE_RECALCULATED'
        AFTER effective_removed_net_weight_g,
    ADD COLUMN record_remark VARCHAR(500) NULL
        AFTER effective_weight_source,
    ADD COLUMN lock_version BIGINT NOT NULL DEFAULT 1
        AFTER record_remark;

UPDATE rec_clean_record
SET effective_removed_net_weight_g =
        CASE
            WHEN recalculated_removed_net_weight_status = 'RELIABLE'
            THEN recalculated_removed_net_weight_g
            ELSE NULL
        END,
    effective_weight_source = 'DEVICE_RECALCULATED',
    lock_version = 1;

ALTER TABLE rec_clean_record
    DROP COLUMN review_status,
    DROP COLUMN review_revision_no,
    DROP COLUMN review_revision_id,
    DROP COLUMN final_recognized_net_weight_kg,
    ADD CONSTRAINT ck_rec_clean_record_effective_weight CHECK (
        effective_weight_source IN (
            'DEVICE_RECALCULATED',
            'MANUAL_SET',
            'MANUAL_CLEARED'
        )
        AND (
            effective_weight_source <> 'MANUAL_SET'
            OR effective_removed_net_weight_g BETWEEN 0 AND 1000000
        )
        AND (
            effective_weight_source <> 'MANUAL_CLEARED'
            OR effective_removed_net_weight_g IS NULL
        )
        AND lock_version >= 1
    ),
    ADD CONSTRAINT ck_rec_clean_record_remark CHECK (
        record_remark IS NULL
        OR CHAR_LENGTH(TRIM(record_remark)) BETWEEN 1 AND 500
    ),
    ADD INDEX ix_rec_clean_record_effective_weight (
        tenant_id,
        organization_id,
        effective_weight_source,
        completed_at,
        clean_record_no
    );

DROP TABLE rec_clean_revision;

CREATE TABLE rec_clean_record_change (
    id BIGINT NOT NULL AUTO_INCREMENT,
    change_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    clean_record_id BIGINT NOT NULL,
    from_version BIGINT NOT NULL,
    to_version BIGINT NOT NULL,
    before_effective_removed_net_weight_g BIGINT NULL,
    before_effective_weight_source
        VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    before_record_remark VARCHAR(500) NULL,
    after_effective_removed_net_weight_g BIGINT NULL,
    after_effective_weight_source
        VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    after_record_remark VARCHAR(500) NULL,
    reason VARCHAR(500) NOT NULL,
    actor_kind VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    platform_admin_id BIGINT NULL,
    staff_account_id BIGINT NULL,
    idempotency_key CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    request_sha256 BINARY(32) NOT NULL,
    changed_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_rec_clean_change_uid UNIQUE (change_uid),
    CONSTRAINT uq_rec_clean_change_record_version
        UNIQUE (clean_record_id, to_version),
    CONSTRAINT uq_rec_clean_change_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_rec_clean_change_idempotency
        UNIQUE (tenant_id, organization_id, idempotency_key),
    CONSTRAINT ck_rec_clean_change_uid_v4 CHECK (
        change_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND idempotency_key REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_rec_clean_change_version CHECK (
        from_version >= 1
        AND to_version = from_version + 1
    ),
    CONSTRAINT ck_rec_clean_change_before CHECK (
        before_effective_weight_source IN (
            'DEVICE_RECALCULATED', 'MANUAL_SET', 'MANUAL_CLEARED'
        )
        AND (
            before_effective_weight_source <> 'MANUAL_SET'
            OR before_effective_removed_net_weight_g
                BETWEEN 0 AND 1000000
        )
        AND (
            before_effective_weight_source <> 'MANUAL_CLEARED'
            OR before_effective_removed_net_weight_g IS NULL
        )
        AND (
            before_record_remark IS NULL
            OR CHAR_LENGTH(TRIM(before_record_remark)) BETWEEN 1 AND 500
        )
    ),
    CONSTRAINT ck_rec_clean_change_after CHECK (
        after_effective_weight_source IN (
            'DEVICE_RECALCULATED', 'MANUAL_SET', 'MANUAL_CLEARED'
        )
        AND (
            after_effective_weight_source <> 'MANUAL_SET'
            OR after_effective_removed_net_weight_g BETWEEN 0 AND 1000000
        )
        AND (
            after_effective_weight_source <> 'MANUAL_CLEARED'
            OR after_effective_removed_net_weight_g IS NULL
        )
        AND (
            after_record_remark IS NULL
            OR CHAR_LENGTH(TRIM(after_record_remark)) BETWEEN 1 AND 500
        )
    ),
    CONSTRAINT ck_rec_clean_change_actor CHECK (
        (
            actor_kind = 'PLATFORM_ADMIN'
            AND platform_admin_id IS NOT NULL
            AND staff_account_id IS NULL
        )
        OR
        (
            actor_kind = 'STAFF'
            AND platform_admin_id IS NULL
            AND staff_account_id IS NOT NULL
        )
    ),
    CONSTRAINT ck_rec_clean_change_reason CHECK (
        CHAR_LENGTH(TRIM(reason)) BETWEEN 1 AND 500
    ),
    CONSTRAINT ck_rec_clean_change_times CHECK (
        created_at >= changed_at
    ),
    CONSTRAINT fk_rec_clean_change_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_clean_change_record
        FOREIGN KEY (tenant_id, organization_id, clean_record_id)
        REFERENCES rec_clean_record (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_clean_change_platform
        FOREIGN KEY (platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_clean_change_staff
        FOREIGN KEY (tenant_id, staff_account_id)
        REFERENCES iam_staff_account (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_clean_change_record (
        tenant_id,
        organization_id,
        clean_record_id,
        to_version DESC
    ),
    INDEX ix_rec_clean_change_platform_fk (platform_admin_id),
    INDEX ix_rec_clean_change_staff_fk (tenant_id, staff_account_id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DELIMITER $$

CREATE TRIGGER trg_rec_clean_record_change_no_update
BEFORE UPDATE ON rec_clean_record_change
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT =
            'rec_clean_record_change is append-only';
END$$

CREATE TRIGGER trg_rec_clean_record_change_no_delete
BEFORE DELETE ON rec_clean_record_change
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT =
            'rec_clean_record_change is append-only';
END$$

DELIMITER ;

ALTER TABLE rec_organization_clean_config
    DROP CHECK ck_rec_clean_config_m0,
    DROP COLUMN review_mode,
    ADD CONSTRAINT ck_rec_clean_config_timeout CHECK (
        operation_timeout_seconds = 1800
    );

-- Publish a review-free configuration generation for every existing
-- organization. Old immutable rows remain valid snapshots for already frozen
-- operations, while new work always reads the new head.
INSERT INTO rec_organization_clean_config (
    tenant_id,
    organization_id,
    version_no,
    content_sha256,
    operation_timeout_seconds,
    publication_source,
    published_by_staff_account_id,
    published_at,
    created_at
)
SELECT organization.tenant_id,
       organization.id,
       COALESCE(MAX(existing.version_no), 0) + 1,
       UNHEX(SHA2(
           '{"operationTimeoutSeconds":1800,"schemaVersion":2}',
           256
       )),
       1800,
       'SYSTEM',
       NULL,
       UTC_TIMESTAMP(3),
       UTC_TIMESTAMP(3)
FROM iam_organization organization
LEFT JOIN rec_organization_clean_config existing
  ON existing.tenant_id = organization.tenant_id
 AND existing.organization_id = organization.id
GROUP BY organization.tenant_id, organization.id;

INSERT INTO rec_organization_clean_config_head (
    organization_id,
    tenant_id,
    current_config_id,
    current_version_no,
    lock_version,
    switched_at,
    updated_at
)
SELECT current_config.organization_id,
       current_config.tenant_id,
       current_config.id,
       current_config.version_no,
       0,
       UTC_TIMESTAMP(3),
       UTC_TIMESTAMP(3)
FROM rec_organization_clean_config current_config
LEFT JOIN rec_organization_clean_config newer
  ON newer.tenant_id = current_config.tenant_id
 AND newer.organization_id = current_config.organization_id
 AND newer.version_no > current_config.version_no
WHERE newer.id IS NULL
ON DUPLICATE KEY UPDATE
    current_config_id = VALUES(current_config_id),
    current_version_no = VALUES(current_version_no),
    lock_version = lock_version + 1,
    switched_at = VALUES(switched_at),
    updated_at = VALUES(updated_at);

INSERT INTO rec_organization_clean_record_counter (
    organization_id,
    tenant_id,
    last_visibility_sequence_no,
    lock_version,
    updated_at
)
SELECT organization.id,
       organization.tenant_id,
       0,
       0,
       UTC_TIMESTAMP(3)
FROM iam_organization organization
LEFT JOIN rec_organization_clean_record_counter counter
  ON counter.organization_id = organization.id
WHERE counter.organization_id IS NULL;

INSERT INTO iam_permission_definition (
    permission_code,
    scope_kind,
    permission_name,
    description,
    enabled,
    created_at
) VALUES
    (
        'clean.edit', 'TENANT', '修改清运记录',
        '直接修改本租户清运记录当前有效数据并保留变更留痕。',
        1, '2026-08-01 00:00:00.000'
    ),
    (
        'clean.edit', 'ORGANIZATION', '修改清运记录',
        '直接修改当前机构清运记录当前有效数据并保留变更留痕。',
        1, '2026-08-01 00:00:00.000'
    );

UPDATE iam_permission_definition
SET description =
        CASE scope_kind
            WHEN 'TENANT' THEN '执行本租户投递和提现初审。'
            ELSE '执行当前机构投递和提现初审。'
        END
WHERE permission_code = 'review.execute'
  AND scope_kind IN ('TENANT', 'ORGANIZATION');
