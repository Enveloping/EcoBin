-- Automatic MCU-restart delivery verdict, separate from the manual quarantine workflow.
-- Immutable issue evidence only: no order, wallet, bag or tare migration/backfill.
CREATE TABLE dev_delivery_issue (
    id BIGINT NOT NULL AUTO_INCREMENT,
    issue_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    asset_id BIGINT NOT NULL,
    delivery_session_id BIGINT NOT NULL,
    original_command_id BIGINT NOT NULL,
    source_inbox_id BIGINT NOT NULL,
    event_payload_sha256 BINARY(32) NOT NULL,
    archive_evidence_sha256 BINARY(32) NOT NULL,
    source_mcu_boot_id BIGINT NOT NULL,
    target_mcu_boot_id BIGINT NOT NULL,
    reason VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    business_value VARCHAR(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    header_json JSON NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_issue_uid UNIQUE (issue_uid),
    CONSTRAINT uq_issue_session UNIQUE (delivery_session_id),
    CONSTRAINT uq_issue_command UNIQUE (original_command_id),
    CONSTRAINT uq_issue_inbox UNIQUE (source_inbox_id),
    CONSTRAINT ck_issue_uid CHECK (issue_uid REGEXP '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'),
    CONSTRAINT ck_issue_boot CHECK (source_mcu_boot_id > 0 AND source_mcu_boot_id < target_mcu_boot_id AND target_mcu_boot_id <= 9007199254740991),
    CONSTRAINT ck_issue_verdict CHECK (reason = 'MCU_RESTART_FINAL_RESULT_UNAVAILABLE' AND business_value = 'NONE'),
    CONSTRAINT fk_issue_session FOREIGN KEY (tenant_id, organization_id, asset_id, delivery_session_id)
        REFERENCES dev_delivery_session (tenant_id, organization_id, asset_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_issue_command FOREIGN KEY (tenant_id, organization_id, asset_id, original_command_id)
        REFERENCES dev_device_command (tenant_id, organization_id, asset_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_issue_inbox FOREIGN KEY (source_inbox_id) REFERENCES ops_inbox_message (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_issue_asset (asset_id, created_at, id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Raw pieces are immutable; incomplete/conflicting artifacts never become trusted results.
CREATE TABLE dev_delivery_issue_part (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    issue_id BIGINT NOT NULL,
    evidence_kind VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    evidence_index BIGINT NOT NULL,
    evidence_sha256 BINARY(32) NOT NULL,
    evidence_size_bytes BIGINT NOT NULL,
    part_count INT NOT NULL,
    part_index INT NOT NULL,
    part_bytes VARBINARY(256) NOT NULL,
    event_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    event_payload_sha256 BINARY(32) NOT NULL,
    source_inbox_id BIGINT NOT NULL,
    created_at DATETIME(3) NOT NULL,
    CONSTRAINT uq_issue_part UNIQUE(issue_id,evidence_kind,evidence_index,part_index),
    CONSTRAINT uq_issue_part_event UNIQUE(event_uid),
    CONSTRAINT uq_issue_part_inbox UNIQUE(source_inbox_id),
    CONSTRAINT ck_issue_part_kind CHECK ((evidence_kind IN ('ARCHIVE_CONTEXT','FINAL_RESULT') AND evidence_index=0) OR (evidence_kind='PROCESS_FACT' AND evidence_index BETWEEN 1 AND 4294967295)),
    CONSTRAINT ck_issue_part_size CHECK (evidence_size_bytes BETWEEN 1 AND 4294967295 AND part_count=CEIL(evidence_size_bytes/256) AND part_index BETWEEN 1 AND part_count AND OCTET_LENGTH(part_bytes)=LEAST(256,evidence_size_bytes-(part_index-1)*256)),
    CONSTRAINT fk_issue_part_parent FOREIGN KEY(issue_id) REFERENCES dev_delivery_issue(id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_issue_part_inbox FOREIGN KEY(source_inbox_id) REFERENCES ops_inbox_message(id) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- A completion witness, not a financial/physical result. No UPDATE privilege is needed.
CREATE TABLE dev_delivery_issue_evidence (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    issue_id BIGINT NOT NULL,
    evidence_kind VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    evidence_index BIGINT NOT NULL,
    evidence_sha256 BINARY(32) NOT NULL,
    evidence_size_bytes BIGINT NOT NULL,
    part_count INT NOT NULL,
    created_at DATETIME(3) NOT NULL,
    CONSTRAINT uq_issue_evidence UNIQUE(issue_id,evidence_kind,evidence_index),
    CONSTRAINT ck_issue_evidence_kind CHECK ((evidence_kind IN ('ARCHIVE_CONTEXT','FINAL_RESULT') AND evidence_index=0) OR (evidence_kind='PROCESS_FACT' AND evidence_index BETWEEN 1 AND 4294967295)),
    CONSTRAINT ck_issue_evidence_size CHECK (evidence_size_bytes BETWEEN 1 AND 4294967295 AND part_count=CEIL(evidence_size_bytes/256)),
    CONSTRAINT fk_issue_evidence_parent FOREIGN KEY(issue_id) REFERENCES dev_delivery_issue(id) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
