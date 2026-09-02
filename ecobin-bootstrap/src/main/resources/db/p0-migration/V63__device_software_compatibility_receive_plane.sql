-- V63: receive-only device software facts and compatibility projection.
--
-- This migration deliberately does not create deployment commands, rollout
-- state, COS grants, or any other path that can ask a device to update.  It
-- only gives the backend an immutable release declaration, an append-only
-- actual-state fact, and a current read projection.  Existing devices are
-- explicitly classified as LEGACY_DIRECT and remain admitted by the legacy
-- rules until a trusted PERMANENT_V1 fact proves the one-way cutover.

CREATE TABLE dev_edge_software_release (
    id BIGINT NOT NULL AUTO_INCREMENT,
    release_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    version_name VARCHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    release_sequence BIGINT UNSIGNED NOT NULL,
    package_sha256 BINARY(32) NOT NULL,
    package_format_version INT UNSIGNED NOT NULL,
    backend_command_contract_version INT UNSIGNED NOT NULL,
    device_event_contract_version INT UNSIGNED NOT NULL,
    communication_business_protocol_major SMALLINT UNSIGNED NOT NULL,
    communication_business_protocol_minor SMALLINT UNSIGNED NOT NULL,
    updater_business_protocol_major SMALLINT UNSIGNED NOT NULL,
    updater_business_protocol_minor SMALLINT UNSIGNED NOT NULL,
    uart_protocol_family VARCHAR(24)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    uart_protocol_major SMALLINT UNSIGNED NULL,
    uart_protocol_minor SMALLINT UNSIGNED NULL,
    required_fixed_frame_revision INT UNSIGNED NULL,
    required_mcu_capability_bitmap_hex CHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    provided_business_capability_bitmap_hex CHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    declaration_sha256 BINARY(32) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_edge_software_release_uid UNIQUE (release_uid),
    CONSTRAINT uq_dev_edge_software_release_version UNIQUE (version_name),
    CONSTRAINT uq_dev_edge_software_release_sequence UNIQUE (release_sequence),
    CONSTRAINT uq_dev_edge_software_release_package UNIQUE (package_sha256),
    CONSTRAINT uq_dev_edge_software_release_declaration
        UNIQUE (declaration_sha256),
    CONSTRAINT ck_dev_edge_software_release_uid CHECK (
        release_uid = LOWER(release_uid)
        AND release_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_edge_software_release_identity CHECK (
        BINARY version_name = BINARY TRIM(version_name)
        AND CHAR_LENGTH(version_name) BETWEEN 1 AND 32
        AND version_name REGEXP
            '^[0-9A-Za-z][0-9A-Za-z._+-]{0,31}$'
        AND release_sequence BETWEEN 1 AND 9007199254740991
        AND package_format_version BETWEEN 1 AND 65535
        AND backend_command_contract_version BETWEEN 1 AND 65535
        AND device_event_contract_version BETWEEN 1 AND 65535
    ),
    CONSTRAINT ck_dev_edge_software_release_protocols CHECK (
        communication_business_protocol_major BETWEEN 1 AND 255
        AND communication_business_protocol_minor BETWEEN 0 AND 255
        AND updater_business_protocol_major BETWEEN 1 AND 255
        AND updater_business_protocol_minor BETWEEN 0 AND 255
    ),
    CONSTRAINT ck_dev_edge_software_release_uart CHECK (
        (
            uart_protocol_family = 'FIXED_FRAME'
            AND uart_protocol_major IS NULL
            AND uart_protocol_minor IS NULL
            AND required_fixed_frame_revision IS NOT NULL
            AND required_fixed_frame_revision BETWEEN 1 AND 255
        )
        OR (
            uart_protocol_family = 'ECOBIN_UART'
            AND uart_protocol_major IS NOT NULL
            AND uart_protocol_minor IS NOT NULL
            AND uart_protocol_major BETWEEN 1 AND 255
            AND uart_protocol_minor BETWEEN 0 AND 255
            AND required_fixed_frame_revision IS NULL
        )
    ),
    CONSTRAINT ck_dev_edge_software_release_capabilities CHECK (
        required_mcu_capability_bitmap_hex REGEXP '^[0-9a-f]{16}$'
        AND provided_business_capability_bitmap_hex REGEXP '^[0-9a-f]{16}$'
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_device_management_profile (
    asset_id BIGINT NOT NULL,
    architecture_generation VARCHAR(24)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    transition_source_event_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    transitioned_at DATETIME(3) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (asset_id),
    CONSTRAINT ck_dev_management_profile_generation CHECK (
        architecture_generation IN ('LEGACY_DIRECT', 'PERMANENT_V1')
        AND (
            (
                architecture_generation = 'LEGACY_DIRECT'
                AND transition_source_event_uid IS NULL
                AND transitioned_at IS NULL
            )
            OR (
                architecture_generation = 'PERMANENT_V1'
                AND transition_source_event_uid IS NOT NULL
                AND transition_source_event_uid REGEXP
                    '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
                AND transitioned_at IS NOT NULL
            )
        )
    ),
    CONSTRAINT ck_dev_management_profile_version CHECK (lock_version >= 0),
    CONSTRAINT ck_dev_management_profile_times CHECK (
        updated_at >= created_at
        AND (transitioned_at IS NULL OR transitioned_at >= created_at)
    ),
    CONSTRAINT fk_dev_management_profile_asset
        FOREIGN KEY (asset_id) REFERENCES dev_device_asset (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_device_software_fact (
    id BIGINT NOT NULL AUTO_INCREMENT,
    event_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_inbox_id BIGINT NOT NULL,
    asset_id BIGINT NOT NULL,
    management_state_sequence BIGINT UNSIGNED NOT NULL,
    architecture_generation VARCHAR(24)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    business_gate_state VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    communication_agent_version VARCHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    management_transport_protocol_major SMALLINT UNSIGNED NOT NULL,
    management_transport_protocol_minor SMALLINT UNSIGNED NOT NULL,
    communication_business_protocol_major SMALLINT UNSIGNED NOT NULL,
    communication_business_protocol_minor SMALLINT UNSIGNED NOT NULL,
    communication_updater_protocol_major SMALLINT UNSIGNED NOT NULL,
    communication_updater_protocol_minor SMALLINT UNSIGNED NOT NULL,
    device_updater_version VARCHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    device_maintenance_protocol_major SMALLINT UNSIGNED NOT NULL,
    device_maintenance_protocol_minor SMALLINT UNSIGNED NOT NULL,
    updater_business_protocol_major SMALLINT UNSIGNED NOT NULL,
    updater_business_protocol_minor SMALLINT UNSIGNED NOT NULL,
    business_package_format_version INT UNSIGNED NOT NULL,
    mcu_package_format_version INT UNSIGNED NOT NULL,
    active_business_release_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    active_business_release_sequence BIGINT UNSIGNED NULL,
    active_business_version_name VARCHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    active_business_package_sha256 BINARY(32) NULL,
    business_process_state VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    business_process_ready TINYINT NOT NULL,
    negotiated_communication_business_major SMALLINT UNSIGNED NULL,
    negotiated_communication_business_minor SMALLINT UNSIGNED NULL,
    negotiated_communication_updater_major SMALLINT UNSIGNED NULL,
    negotiated_communication_updater_minor SMALLINT UNSIGNED NULL,
    negotiated_updater_business_major SMALLINT UNSIGNED NULL,
    negotiated_updater_business_minor SMALLINT UNSIGNED NULL,
    mcu_firmware_version VARCHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    mcu_firmware_version_code BIGINT UNSIGNED NULL,
    mcu_firmware_identity_hex CHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    mcu_fixed_frame_revision INT UNSIGNED NULL,
    uart_state VARCHAR(20)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    uart_protocol_family VARCHAR(24)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    uart_protocol_major SMALLINT UNSIGNED NULL,
    uart_protocol_minor SMALLINT UNSIGNED NULL,
    capability_bitmap_hex CHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    payload_sha256 BINARY(32) NOT NULL,
    normalized_payload JSON NOT NULL,
    observed_at DATETIME(3) NULL,
    received_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_software_fact_event UNIQUE (event_uid),
    CONSTRAINT uq_dev_software_fact_inbox UNIQUE (source_inbox_id),
    CONSTRAINT uq_dev_software_fact_sequence
        UNIQUE (asset_id, management_state_sequence),
    CONSTRAINT ck_dev_software_fact_uid CHECK (
        event_uid = LOWER(event_uid)
        AND event_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_software_fact_sequence CHECK (
        management_state_sequence BETWEEN 1 AND 9007199254740991
        AND architecture_generation = 'PERMANENT_V1'
    ),
    CONSTRAINT ck_dev_software_fact_gate CHECK (
        business_gate_state IN ('OPEN', 'DRAINING', 'MAINTENANCE', 'LOCKED')
    ),
    CONSTRAINT ck_dev_software_fact_versions CHECK (
        BINARY communication_agent_version =
            BINARY TRIM(communication_agent_version)
        AND CHAR_LENGTH(communication_agent_version) BETWEEN 1 AND 32
        AND communication_agent_version REGEXP
            '^[0-9A-Za-z][0-9A-Za-z._+-]{0,31}$'
        AND BINARY device_updater_version = BINARY TRIM(device_updater_version)
        AND CHAR_LENGTH(device_updater_version) BETWEEN 1 AND 32
        AND device_updater_version REGEXP
            '^[0-9A-Za-z][0-9A-Za-z._+-]{0,31}$'
    ),
    CONSTRAINT ck_dev_software_fact_protocols CHECK (
        management_transport_protocol_major BETWEEN 1 AND 255
        AND management_transport_protocol_minor BETWEEN 0 AND 255
        AND communication_business_protocol_major BETWEEN 1 AND 255
        AND communication_business_protocol_minor BETWEEN 0 AND 255
        AND communication_updater_protocol_major BETWEEN 1 AND 255
        AND communication_updater_protocol_minor BETWEEN 0 AND 255
        AND device_maintenance_protocol_major BETWEEN 1 AND 255
        AND device_maintenance_protocol_minor BETWEEN 0 AND 255
        AND updater_business_protocol_major BETWEEN 1 AND 255
        AND updater_business_protocol_minor BETWEEN 0 AND 255
        AND business_package_format_version BETWEEN 1 AND 65535
        AND mcu_package_format_version BETWEEN 1 AND 65535
        AND (
            (
                negotiated_communication_business_major IS NULL
                AND negotiated_communication_business_minor IS NULL
            )
            OR (
                negotiated_communication_business_major IS NOT NULL
                AND negotiated_communication_business_minor IS NOT NULL
                AND negotiated_communication_business_major
                    BETWEEN 1 AND 255
                AND negotiated_communication_business_minor BETWEEN 0 AND 255
            )
        )
        AND (
            (
                negotiated_communication_updater_major IS NULL
                AND negotiated_communication_updater_minor IS NULL
            )
            OR (
                negotiated_communication_updater_major IS NOT NULL
                AND negotiated_communication_updater_minor IS NOT NULL
                AND negotiated_communication_updater_major
                    BETWEEN 1 AND 255
                AND negotiated_communication_updater_minor BETWEEN 0 AND 255
            )
        )
        AND (
            (
                negotiated_updater_business_major IS NULL
                AND negotiated_updater_business_minor IS NULL
            )
            OR (
                negotiated_updater_business_major IS NOT NULL
                AND negotiated_updater_business_minor IS NOT NULL
                AND negotiated_updater_business_major
                    BETWEEN 1 AND 255
                AND negotiated_updater_business_minor BETWEEN 0 AND 255
            )
        )
    ),
    CONSTRAINT ck_dev_software_fact_release CHECK (
        (
            active_business_release_uid IS NULL
            AND active_business_release_sequence IS NULL
            AND active_business_version_name IS NULL
            AND active_business_package_sha256 IS NULL
        )
        OR (
            active_business_release_uid IS NOT NULL
            AND active_business_release_sequence IS NOT NULL
            AND active_business_version_name IS NOT NULL
            AND active_business_package_sha256 IS NOT NULL
            AND active_business_release_uid REGEXP
                '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            AND active_business_release_sequence
                BETWEEN 1 AND 9007199254740991
            AND BINARY active_business_version_name =
                BINARY TRIM(active_business_version_name)
            AND CHAR_LENGTH(active_business_version_name) BETWEEN 1 AND 32
            AND active_business_version_name REGEXP
                '^[0-9A-Za-z][0-9A-Za-z._+-]{0,31}$'
        )
    ),
    CONSTRAINT ck_dev_software_fact_process CHECK (
        business_process_state IN (
            'STOPPED', 'STARTING', 'RUNNING', 'FAILED'
        )
        AND business_process_ready IN (0, 1)
        AND (
            business_process_ready = 0
            OR (
                business_process_state = 'RUNNING'
                AND active_business_release_uid IS NOT NULL
                AND negotiated_communication_business_major IS NOT NULL
                AND negotiated_updater_business_major IS NOT NULL
            )
        )
    ),
    CONSTRAINT ck_dev_software_fact_mcu_identity CHECK (
        (
            mcu_firmware_version IS NULL
            AND mcu_firmware_version_code IS NULL
            AND mcu_firmware_identity_hex IS NULL
            AND mcu_fixed_frame_revision IS NULL
        )
        OR (
            mcu_firmware_version IS NOT NULL
            AND mcu_firmware_version_code IS NOT NULL
            AND mcu_firmware_identity_hex IS NOT NULL
            AND mcu_fixed_frame_revision IS NOT NULL
            AND BINARY mcu_firmware_version = BINARY TRIM(mcu_firmware_version)
            AND CHAR_LENGTH(mcu_firmware_version) BETWEEN 1 AND 32
            AND mcu_firmware_version REGEXP
                '^[0-9A-Za-z][0-9A-Za-z._+-]{0,31}$'
            AND mcu_firmware_version_code BETWEEN 1 AND 4294967295
            AND mcu_firmware_identity_hex REGEXP '^[0-9a-f]{16}$'
            AND mcu_fixed_frame_revision BETWEEN 1 AND 255
        )
    ),
    CONSTRAINT ck_dev_software_fact_uart CHECK (
        uart_state IN (
            'DISCONNECTED', 'NEGOTIATING', 'READY', 'INCOMPATIBLE', 'FAULT'
        )
        AND uart_protocol_family IN ('FIXED_FRAME', 'ECOBIN_UART', 'UNKNOWN')
        AND capability_bitmap_hex REGEXP '^[0-9a-f]{16}$'
        AND (
            (
                uart_protocol_family = 'ECOBIN_UART'
                AND uart_protocol_major IS NOT NULL
                AND uart_protocol_minor IS NOT NULL
                AND uart_protocol_major BETWEEN 1 AND 255
                AND uart_protocol_minor BETWEEN 0 AND 255
            )
            OR (
                uart_protocol_family IN ('FIXED_FRAME', 'UNKNOWN')
                AND uart_protocol_major IS NULL
                AND uart_protocol_minor IS NULL
            )
        )
    ),
    CONSTRAINT ck_dev_software_fact_times CHECK (
        created_at = received_at
    ),
    CONSTRAINT fk_dev_software_fact_inbox
        FOREIGN KEY (source_inbox_id) REFERENCES ops_inbox_message (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_software_fact_asset
        FOREIGN KEY (asset_id) REFERENCES dev_device_asset (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_software_fact_asset (
        asset_id, management_state_sequence DESC, id DESC
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_device_compatibility_projection (
    asset_id BIGINT NOT NULL,
    architecture_generation VARCHAR(24)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    latest_software_fact_id BIGINT NULL,
    source_event_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    management_state_sequence BIGINT UNSIGNED NULL,
    compatibility_status VARCHAR(24)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    business_admission_status VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    primary_reason_code VARCHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    primary_reason_message VARCHAR(500) NULL,
    reasons_json JSON NOT NULL,
    capabilities_json JSON NOT NULL,
    observed_at DATETIME(3) NULL,
    received_at DATETIME(3) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (asset_id),
    CONSTRAINT ck_dev_compatibility_generation CHECK (
        architecture_generation IN ('LEGACY_DIRECT', 'PERMANENT_V1')
        AND (
            (
                architecture_generation = 'LEGACY_DIRECT'
                AND latest_software_fact_id IS NULL
                AND source_event_uid IS NULL
                AND management_state_sequence IS NULL
                AND compatibility_status = 'FULLY_COMPATIBLE'
                AND business_admission_status = 'ACCEPTING'
            )
            OR (
                architecture_generation = 'PERMANENT_V1'
                AND latest_software_fact_id IS NOT NULL
                AND source_event_uid IS NOT NULL
                AND source_event_uid REGEXP
                    '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
                AND management_state_sequence IS NOT NULL
                AND management_state_sequence
                    BETWEEN 1 AND 9007199254740991
                AND received_at IS NOT NULL
            )
        )
    ),
    CONSTRAINT ck_dev_compatibility_status CHECK (
        compatibility_status IN (
            'FULLY_COMPATIBLE', 'BASE_COMPATIBLE',
            'INCOMPATIBLE', 'UNKNOWN'
        )
        AND business_admission_status IN ('ACCEPTING', 'PAUSED', 'UNKNOWN')
    ),
    CONSTRAINT ck_dev_compatibility_reason CHECK (
        JSON_TYPE(reasons_json) = 'ARRAY'
        AND JSON_TYPE(capabilities_json) = 'OBJECT'
        AND (
            (primary_reason_code IS NULL AND primary_reason_message IS NULL)
            OR (
                primary_reason_code IS NOT NULL
                AND primary_reason_message IS NOT NULL
                AND primary_reason_code REGEXP '^[A-Z][A-Z0-9_]{0,63}$'
                AND CHAR_LENGTH(TRIM(primary_reason_message)) BETWEEN 1 AND 500
            )
        )
    ),
    CONSTRAINT ck_dev_compatibility_version CHECK (lock_version >= 0),
    CONSTRAINT ck_dev_compatibility_times CHECK (
        updated_at >= created_at
        AND (received_at IS NULL OR received_at >= created_at)
    ),
    CONSTRAINT fk_dev_compatibility_asset
        FOREIGN KEY (asset_id) REFERENCES dev_device_asset (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_compatibility_fact
        FOREIGN KEY (latest_software_fact_id)
        REFERENCES dev_device_software_fact (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_compatibility_status (
        architecture_generation, compatibility_status,
        business_admission_status, asset_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO dev_device_management_profile (
    asset_id, architecture_generation,
    transition_source_event_uid, transitioned_at,
    lock_version, created_at, updated_at
)
SELECT asset.id, 'LEGACY_DIRECT', NULL, NULL, 0,
       asset.created_at, GREATEST(asset.created_at, asset.updated_at)
FROM dev_device_asset asset;

INSERT INTO dev_device_compatibility_projection (
    asset_id, architecture_generation,
    latest_software_fact_id, source_event_uid,
    management_state_sequence,
    compatibility_status, business_admission_status,
    primary_reason_code, primary_reason_message,
    reasons_json, capabilities_json,
    observed_at, received_at,
    lock_version, created_at, updated_at
)
SELECT asset.id, 'LEGACY_DIRECT', NULL, NULL, NULL,
       'FULLY_COMPATIBLE', 'ACCEPTING', NULL, NULL,
       JSON_ARRAY(), JSON_OBJECT('legacyDirect', TRUE),
       NULL, NULL, 0,
       asset.created_at, GREATEST(asset.created_at, asset.updated_at)
FROM dev_device_asset asset;

DELIMITER $$

CREATE DEFINER = 'ecobin_trigger_definer'@'%'
TRIGGER trg_dev_asset_v63_management_defaults
AFTER INSERT ON dev_device_asset
FOR EACH ROW
BEGIN
    INSERT INTO dev_device_management_profile (
        asset_id, architecture_generation,
        transition_source_event_uid, transitioned_at,
        lock_version, created_at, updated_at
    ) VALUES (
        NEW.id, 'LEGACY_DIRECT', NULL, NULL,
        0, NEW.created_at, NEW.updated_at
    );

    INSERT INTO dev_device_compatibility_projection (
        asset_id, architecture_generation,
        latest_software_fact_id, source_event_uid,
        management_state_sequence,
        compatibility_status, business_admission_status,
        primary_reason_code, primary_reason_message,
        reasons_json, capabilities_json,
        observed_at, received_at,
        lock_version, created_at, updated_at
    ) VALUES (
        NEW.id, 'LEGACY_DIRECT', NULL, NULL, NULL,
        'FULLY_COMPATIBLE', 'ACCEPTING', NULL, NULL,
        JSON_ARRAY(), JSON_OBJECT('legacyDirect', TRUE),
        NULL, NULL, 0, NEW.created_at, NEW.updated_at
    );
END$$

CREATE DEFINER = 'ecobin_trigger_definer'@'%'
TRIGGER trg_dev_management_profile_v63_no_downgrade
BEFORE UPDATE ON dev_device_management_profile
FOR EACH ROW
BEGIN
    IF OLD.asset_id <> NEW.asset_id THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'device management profile identity is immutable';
    END IF;
    IF OLD.architecture_generation = 'PERMANENT_V1'
        AND (
            NEW.architecture_generation <> 'PERMANENT_V1'
            OR NOT (
                OLD.transition_source_event_uid
                    <=> NEW.transition_source_event_uid
            )
            OR NOT (OLD.transitioned_at <=> NEW.transitioned_at)
        ) THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'permanent device management transition is immutable';
    END IF;
END$$

CREATE DEFINER = 'ecobin_trigger_definer'@'%'
TRIGGER trg_dev_compatibility_projection_v63_no_downgrade
BEFORE UPDATE ON dev_device_compatibility_projection
FOR EACH ROW
BEGIN
    IF OLD.asset_id <> NEW.asset_id THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'device compatibility projection identity is immutable';
    END IF;
    IF OLD.architecture_generation = 'PERMANENT_V1'
        AND NEW.architecture_generation <> 'PERMANENT_V1' THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'permanent compatibility projection cannot downgrade';
    END IF;
    IF OLD.management_state_sequence IS NOT NULL
        AND (
            NEW.management_state_sequence IS NULL
            OR NEW.management_state_sequence < OLD.management_state_sequence
        ) THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'device management state sequence cannot regress';
    END IF;
END$$

CREATE DEFINER = 'ecobin_trigger_definer'@'%'
TRIGGER trg_dev_edge_software_release_v63_immutable
BEFORE UPDATE ON dev_edge_software_release
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'edge software release declarations are immutable';
END$$

CREATE DEFINER = 'ecobin_trigger_definer'@'%'
TRIGGER trg_dev_edge_software_release_v63_no_delete
BEFORE DELETE ON dev_edge_software_release
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'edge software release declarations cannot be deleted';
END$$

DELIMITER ;
