-- Platform-issued EB1 bag labels.  A batch is printing history only: labels
-- are not tenant/organization inventory and become rec_bag rows only when a
-- real installation is admitted by the existing factory/cleaning flows.

CREATE TABLE rec_bag_label_batch (
    id BIGINT NOT NULL AUTO_INCREMENT,
    batch_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    operation_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    key_id VARCHAR(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    label_count SMALLINT UNSIGNED NOT NULL,
    request_sha256 BINARY(32) NOT NULL,
    created_by_platform_admin_id BIGINT NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_rec_bag_label_batch_uid UNIQUE (batch_uid),
    CONSTRAINT uq_rec_bag_label_batch_operation UNIQUE (operation_uid),
    CONSTRAINT ck_rec_bag_label_batch_uid_v4 CHECK (
        batch_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_rec_bag_label_batch_operation_v4 CHECK (
        operation_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_rec_bag_label_batch_key CHECK (
        key_id REGEXP '^K[0-9A-Z]{1,6}$'
    ),
    CONSTRAINT ck_rec_bag_label_batch_count CHECK (
        label_count BETWEEN 1 AND 100
    ),
    CONSTRAINT fk_rec_bag_label_batch_admin
        FOREIGN KEY (created_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_bag_label_batch_created (
        created_at DESC, id DESC
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE rec_bag_label_item (
    id BIGINT NOT NULL AUTO_INCREMENT,
    batch_id BIGINT NOT NULL,
    sequence_no SMALLINT UNSIGNED NOT NULL,
    bag_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_rec_bag_label_item_code UNIQUE (bag_code),
    CONSTRAINT uq_rec_bag_label_item_sequence
        UNIQUE (batch_id, sequence_no),
    CONSTRAINT ck_rec_bag_label_item_sequence CHECK (
        sequence_no BETWEEN 1 AND 100
    ),
    CONSTRAINT ck_rec_bag_label_item_eb1 CHECK (
        bag_code REGEXP
            '^EB1_K[0-9A-Z]{1,6}_[0-9A-HJKMNP-TV-Z]{26}_[0-9A-HJKMNP-TV-Z]{20}$'
    ),
    CONSTRAINT fk_rec_bag_label_item_batch
        FOREIGN KEY (batch_id)
        REFERENCES rec_bag_label_batch (id)
        ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
