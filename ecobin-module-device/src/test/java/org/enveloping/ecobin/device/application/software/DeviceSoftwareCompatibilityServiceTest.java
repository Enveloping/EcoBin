package org.enveloping.ecobin.device.application.software;

import org.enveloping.ecobin.framework.reliability.UntrustedInboxSourceException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

import java.math.BigDecimal;
import java.math.BigInteger;
import java.time.LocalDateTime;
import java.util.HexFormat;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class DeviceSoftwareCompatibilityServiceTest {

    private static final String RELEASE_UID =
            "8d000000-0000-4000-8000-000000000001";
    private static final String PACKAGE_SHA = "d".repeat(64);

    private JdbcTemplate jdbc;
    private JsonMapper objectMapper;
    private DeviceSoftwareCompatibilityService service;

    @BeforeEach
    void setUp() {
        DriverManagerDataSource dataSource = new DriverManagerDataSource();
        dataSource.setDriverClassName("org.h2.Driver");
        dataSource.setUrl("jdbc:h2:mem:software_compatibility_"
                + UUID.randomUUID()
                + ";MODE=MySQL;DB_CLOSE_DELAY=-1");
        jdbc = new JdbcTemplate(dataSource);
        objectMapper = JsonMapper.builder().build();
        createSchema();
        jdbc.update("""
                INSERT INTO dev_device_management_profile (
                    asset_id, architecture_generation,
                    transition_source_event_uid, transitioned_at,
                    lock_version, created_at, updated_at
                ) VALUES (1, 'LEGACY_DIRECT', NULL, NULL, 0, ?, ?)
                """, now(), now());
        jdbc.update("""
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
                    1, 'LEGACY_DIRECT', NULL, NULL, NULL,
                    'FULLY_COMPATIBLE', 'ACCEPTING', NULL, NULL,
                    '[]', '{"legacyDirect":true}', NULL, NULL, 0, ?, ?
                )
                """, now(), now());
        service = new DeviceSoftwareCompatibilityService(
                jdbc, objectMapper);
    }

    @Test
    void knownInstalledReleaseProjectsFullCompatibilityAndAdmission() {
        registerRelease();

        DeviceSoftwareCompatibilityService.ApplyResult result =
                apply(10, event(7, "OPEN"));

        assertThat(result.factInserted()).isTrue();
        assertThat(result.projectionChanged()).isTrue();
        assertThat(value("architecture_generation"))
                .isEqualTo("PERMANENT_V1");
        assertThat(value("compatibility_status"))
                .isEqualTo("FULLY_COMPATIBLE");
        assertThat(value("business_admission_status"))
                .isEqualTo("ACCEPTING");
        assertThat(value("reasons_json")).isEqualTo("[]");
        assertThat(jdbc.queryForObject(
                "SELECT COUNT(*) FROM dev_device_software_fact",
                Integer.class)).isEqualTo(1);
    }

    @Test
    void periodicHealthyFactCannotReopenBusinessDuringAnActiveUpdate() {
        registerRelease();
        jdbc.update("""
                INSERT INTO dev_edge_software_deployment (
                    asset_id, deployment_status
                ) VALUES (1, 'DOWNLOADING')
                """);

        apply(10, event(7, "OPEN"));

        assertThat(value("compatibility_status"))
                .isEqualTo("FULLY_COMPATIBLE");
        assertThat(value("business_admission_status"))
                .isEqualTo("PAUSED");
        assertThat(value("reasons_json"))
                .contains("BUSINESS_RUNTIME_UPDATE_ACTIVE")
                .contains("业务程序正在更新");
    }

    @ParameterizedTest
    @ValueSource(strings = {"SUCCEEDED", "LOCAL_CANCELLED"})
    void terminalUpdateReassessesTheLatestFactAndRemovesItsTemporaryHold(String terminalStatus) {
        registerRelease();
        jdbc.update("""
                INSERT INTO dev_edge_software_deployment (
                    asset_id, deployment_status
                ) VALUES (1, ?)
                """, "LOCAL_CANCELLED".equals(terminalStatus) ? "QUEUED" : "OBSERVING");
        apply(10, event(7, "OPEN"));
        assertThat(value("business_admission_status")).isEqualTo("PAUSED");
        assertThat(value("reasons_json"))
                .contains("BUSINESS_RUNTIME_UPDATE_ACTIVE");

        jdbc.update("""
                UPDATE dev_edge_software_deployment
                SET deployment_status = ?
                WHERE asset_id = 1
                """, terminalStatus);
        service.reassessLatestFact(1, now().plusMinutes(30));

        assertThat(value("compatibility_status"))
                .isEqualTo("FULLY_COMPATIBLE");
        assertThat(value("business_admission_status"))
                .isEqualTo("ACCEPTING");
        assertThat(value("reasons_json"))
                .doesNotContain("BUSINESS_RUNTIME_UPDATE_ACTIVE");
    }

    @Test
    void unknownReleaseIsPersistedAndProjectedAsUnknown() {
        apply(10, event(7, "OPEN"));

        assertThat(jdbc.queryForObject(
                "SELECT COUNT(*) FROM dev_device_software_fact",
                Integer.class)).isEqualTo(1);
        assertThat(value("compatibility_status")).isEqualTo("UNKNOWN");
        assertThat(value("business_admission_status")).isEqualTo("UNKNOWN");
        assertThat(value("reasons_json"))
                .contains("BUSINESS_RELEASE_NOT_REGISTERED")
                .contains("当前业务程序尚未登记");
    }

    @Test
    void healthyImageBridgeRemainsCompatibleForItsFirstBusinessUpdate() {
        ObjectNode event = event(7, "OPEN");
        ObjectNode payload = (ObjectNode) event.path("payload");
        payload.putNull("activeBusinessRelease");
        ((ObjectNode) payload.path("communicationAgent"))
                .put("versionName", "communication-20260906-31");
        ((ObjectNode) payload.path("deviceUpdater"))
                .put("versionName", "updater-20260906-31");

        apply(10, event);

        assertThat(value("compatibility_status"))
                .isEqualTo("FULLY_COMPATIBLE");
        assertThat(value("business_admission_status"))
                .isEqualTo("ACCEPTING");
        assertThat(value("reasons_json"))
                .contains("IMAGE_BRIDGE_BASELINE")
                .contains("镜像内置业务程序");
    }

    @Test
    void differentImageGenerationsCannotClaimAReadyImageBridge() {
        ObjectNode event = event(7, "OPEN");
        ObjectNode payload = (ObjectNode) event.path("payload");
        payload.putNull("activeBusinessRelease");
        ((ObjectNode) payload.path("communicationAgent"))
                .put("versionName", "communication-20260906-30");
        ((ObjectNode) payload.path("deviceUpdater"))
                .put("versionName", "updater-20260906-31");

        assertThatThrownBy(() -> apply(10, event))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining(
                        "trusted release or image bridge identity");
    }

    @Test
    void closedDeviceGatePausesBusinessWithoutChangingCompatibility() {
        registerRelease();

        apply(10, event(7, "DRAINING"));

        assertThat(value("compatibility_status"))
                .isEqualTo("FULLY_COMPATIBLE");
        assertThat(value("business_admission_status")).isEqualTo("PAUSED");
        assertThat(value("reasons_json"))
                .contains("DEVICE_DRAINING")
                .contains("等待现有作业结束");
    }

    @Test
    void explicitMaintenanceGatePausesEvenWhenCompatibilityIsUnknown() {
        apply(10, event(7, "MAINTENANCE"));

        assertThat(value("compatibility_status")).isEqualTo("UNKNOWN");
        assertThat(value("business_admission_status")).isEqualTo("PAUSED");
        assertThat(value("primary_reason_code"))
                .isEqualTo("DEVICE_IN_MAINTENANCE");
        assertThat(value("reasons_json"))
                .contains("DEVICE_IN_MAINTENANCE")
                .contains("BUSINESS_RELEASE_NOT_REGISTERED");
    }

    @Test
    void explicitGateReasonPrecedesOptionalUpdateLimitations() {
        registerRelease();
        ObjectNode event = event(7, "DRAINING");
        ((ObjectNode) event.path("payload").path("deviceUpdater"))
                .put("mcuPackageFormatVersion", 2);

        apply(10, event);

        assertThat(value("compatibility_status"))
                .isEqualTo("BASE_COMPATIBLE");
        assertThat(value("business_admission_status")).isEqualTo("PAUSED");
        assertThat(value("primary_reason_code"))
                .isEqualTo("DEVICE_DRAINING");
    }

    @Test
    void incompatibleReasonPrecedesAnUnknownReleaseReason() {
        ObjectNode event = event(7, "OPEN");
        ((ObjectNode) event.path("payload").path("communicationAgent"))
                .put("managementTransportProtocolMajor", 2);

        apply(10, event);

        assertThat(value("compatibility_status"))
                .isEqualTo("INCOMPATIBLE");
        assertThat(value("business_admission_status")).isEqualTo("PAUSED");
        assertThat(value("primary_reason_code"))
                .isEqualTo("MANAGEMENT_TRANSPORT_PROTOCOL_MISMATCH");
    }

    @Test
    void unavailableUartDoesNotTurnAnUnreadCapabilityIntoAMismatch() {
        registerRelease();
        ObjectNode event = event(7, "OPEN");
        ObjectNode payload = (ObjectNode) event.path("payload");
        payload.put("uartState", "DISCONNECTED");
        payload.put("capabilityBitmapHex", "0000000000000000");

        apply(10, event);

        assertThat(value("compatibility_status")).isEqualTo("UNKNOWN");
        assertThat(value("business_admission_status")).isEqualTo("UNKNOWN");
        assertThat(value("reasons_json"))
                .contains("UART_PROTOCOL_NOT_READY")
                .doesNotContain("MCU_CAPABILITY_MISMATCH");
    }

    @Test
    void unnegotiatedPairsAreStoredAsNullAndYieldUnknown() {
        registerRelease();
        ObjectNode event = event(7, "OPEN");
        ObjectNode payload = (ObjectNode) event.get("payload");
        payload.put("businessReady", false);
        ObjectNode negotiated = (ObjectNode) payload.get(
                "negotiatedProtocols");
        negotiated.put("agentBusinessNegotiated", false)
                .put("agentBusinessMajor", 0)
                .put("agentBusinessMinor", 0)
                .put("agentUpdaterNegotiated", false)
                .put("agentUpdaterMajor", 0)
                .put("agentUpdaterMinor", 0)
                .put("updaterBusinessNegotiated", false)
                .put("updaterBusinessMajor", 0)
                .put("updaterBusinessMinor", 0);

        apply(10, event);

        assertThat(jdbc.queryForObject("""
                SELECT negotiated_communication_business_major
                FROM dev_device_software_fact
                """, Integer.class)).isNull();
        assertThat(value("compatibility_status")).isEqualTo("UNKNOWN");
        assertThat(value("reasons_json"))
                .contains("AGENT_BUSINESS_PROTOCOL_NOT_NEGOTIATED");
    }

    @Test
    void olderFactIsAppendedWithoutRegressingCurrentProjection() {
        registerRelease();
        apply(10, event(7, "OPEN"));
        ObjectNode older = event(6, "LOCKED");
        older.put("eventUid", "8d000000-0000-4000-8000-000000000006");
        older.put("payloadSha256", "e".repeat(64));

        DeviceSoftwareCompatibilityService.ApplyResult result =
                apply(11, older);

        assertThat(result.factInserted()).isTrue();
        assertThat(result.projectionChanged()).isFalse();
        assertThat(jdbc.queryForObject(
                "SELECT COUNT(*) FROM dev_device_software_fact",
                Integer.class)).isEqualTo(2);
        assertThat(jdbc.queryForObject("""
                SELECT management_state_sequence
                FROM dev_device_compatibility_projection
                WHERE asset_id = 1
                """, Long.class)).isEqualTo(7L);
        assertThat(value("business_admission_status"))
                .isEqualTo("ACCEPTING");
    }

    @Test
    void conflictingContentCannotReuseManagementSequence() {
        registerRelease();
        apply(10, event(7, "OPEN"));
        ObjectNode conflict = event(7, "LOCKED");
        conflict.put("eventUid", "8d000000-0000-4000-8000-000000000007");
        conflict.put("payloadSha256", "e".repeat(64));

        assertThatThrownBy(() -> apply(11, conflict))
                .isInstanceOf(UntrustedInboxSourceException.class)
                .hasMessageContaining("conflicting facts");
    }

    @Test
    void sameEventMayBeReliablyRedeliveredThroughANewInbox() {
        registerRelease();
        ObjectNode event = event(7, "OPEN");
        apply(10, event);

        DeviceSoftwareCompatibilityService.ApplyResult result =
                apply(11, event);

        assertThat(result.changed()).isFalse();
        assertThat(jdbc.queryForObject(
                "SELECT COUNT(*) FROM dev_device_software_fact",
                Integer.class)).isEqualTo(1);
    }

    @Test
    void mysqlUnsignedProjectionSequenceDoesNotDependOnConcreteJdbcType() {
        assertThat(DeviceSoftwareCompatibilityService
                .nullableProjectionSequence(
                        new BigInteger("9000000000004")))
                .isEqualTo(9_000_000_000_004L);
        assertThat(DeviceSoftwareCompatibilityService
                .nullableProjectionSequence(null))
                .isNull();
    }

    @Test
    void mysqlUnsignedIntegerDoesNotDependOnConcreteJdbcType() {
        assertThat(DeviceSoftwareCompatibilityService
                .nullableJdbcInteger(2L))
                .isEqualTo(2);
        assertThat(DeviceSoftwareCompatibilityService
                .nullableJdbcInteger(new BigInteger("255")))
                .isEqualTo(255);
        assertThat(DeviceSoftwareCompatibilityService
                .nullableJdbcInteger(null))
                .isNull();
        assertThatThrownBy(() -> DeviceSoftwareCompatibilityService
                .nullableJdbcInteger(new BigDecimal("2.5")))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("outside the supported range");
    }

    @Test
    void decimalEncodedWholeManagementSequenceIsAccepted() {
        registerRelease();
        ObjectNode event = event(10, "OPEN");
        ((ObjectNode) event.path("payload")).put(
                "managementStateSequence", new BigDecimal("10.0"));

        apply(10, event);

        assertThat(jdbc.queryForObject("""
                SELECT management_state_sequence
                FROM dev_device_compatibility_projection
                WHERE asset_id = 1
                """, Long.class)).isEqualTo(10L);
    }

    @Test
    void fractionalManagementSequenceRemainsInvalid() {
        ObjectNode event = event(10, "OPEN");
        ((ObjectNode) event.path("payload")).put(
                "managementStateSequence", new BigDecimal("10.5"));

        assertThatThrownBy(() -> apply(10, event))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("managementStateSequence");
    }

    private DeviceSoftwareCompatibilityService.ApplyResult apply(
            long inboxId,
            JsonNode event) {
        return service.apply(inboxId, 1, event, event, now());
    }

    private String value(String column) {
        return jdbc.queryForObject(
                "SELECT " + column
                        + " FROM dev_device_compatibility_projection"
                        + " WHERE asset_id = 1",
                String.class);
    }

    private void registerRelease() {
        jdbc.update("""
                INSERT INTO dev_edge_software_release (
                    release_uid, version_name, release_sequence,
                    package_sha256, package_format_version,
                    backend_command_contract_version,
                    device_event_contract_version,
                    communication_business_protocol_major,
                    communication_business_protocol_minor,
                    updater_business_protocol_major,
                    updater_business_protocol_minor,
                    uart_protocol_family,
                    uart_protocol_major, uart_protocol_minor,
                    required_fixed_frame_revision,
                    required_mcu_capability_bitmap_hex
                ) VALUES (?, '1.0.0-rc.3', 12, ?, 1, 2, 2,
                          1, 0, 1, 0,
                          'ECOBIN_UART', 1, 0, NULL,
                          '0000000000000001')
                """,
                RELEASE_UID,
                HexFormat.of().parseHex(PACKAGE_SHA));
    }

    private ObjectNode event(long sequence, String gate) {
        return (ObjectNode) objectMapper.readTree("""
                {
                  "schemaVersion": 2,
                  "eventUid": "8d000000-0000-4000-8000-000000000002",
                  "eventType": "DEVICE_SOFTWARE_STATE_REPORTED",
                  "occurredAt": "2026-07-24T01:00:30.000Z",
                  "payloadSha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                  "payload": {
                    "managementStateSequence": %d,
                    "managementArchitectureGeneration": "PERMANENT_V1",
                    "businessAdmissionState": "%s",
                    "communicationAgent": {
                      "versionName": "1.0.0",
                      "managementTransportProtocolMajor": 1,
                      "managementTransportProtocolMinor": 0,
                      "businessLocalProtocolMajor": 1,
                      "businessLocalProtocolMinor": 0,
                      "updaterLocalProtocolMajor": 1,
                      "updaterLocalProtocolMinor": 0
                    },
                    "deviceUpdater": {
                      "versionName": "1.0.0",
                      "deviceMaintenanceProtocolMajor": 1,
                      "deviceMaintenanceProtocolMinor": 0,
                      "businessLocalProtocolMajor": 1,
                      "businessLocalProtocolMinor": 0,
                      "businessPackageFormatVersion": 1,
                      "mcuPackageFormatVersion": 1
                    },
                    "activeBusinessRelease": {
                      "releaseUid": "%s",
                      "releaseSequence": 12,
                      "versionName": "1.0.0-rc.3",
                      "packageSha256": "%s"
                    },
                    "businessProcessState": "RUNNING",
                    "businessReady": true,
                    "negotiatedProtocols": {
                      "agentBusinessNegotiated": true,
                      "agentBusinessMajor": 1,
                      "agentBusinessMinor": 0,
                      "agentUpdaterNegotiated": true,
                      "agentUpdaterMajor": 1,
                      "agentUpdaterMinor": 0,
                      "updaterBusinessNegotiated": true,
                      "updaterBusinessMajor": 1,
                      "updaterBusinessMinor": 0
                    },
                    "mcuFirmware": {
                      "versionName": "2.1.0",
                      "versionCode": 20100,
                      "identityHex": "0123456789abcdef",
                      "fixedFrameRevision": 2
                    },
                    "uartState": "READY",
                    "uartProtocol": {"major": 1, "minor": 0},
                    "capabilityBitmapHex": "0000000000001fff"
                  }
                }
                """.formatted(sequence, gate, RELEASE_UID, PACKAGE_SHA));
    }

    private void createSchema() {
        jdbc.execute("""
                CREATE TABLE dev_edge_software_release (
                    release_uid VARCHAR(36) PRIMARY KEY,
                    version_name VARCHAR(64) NOT NULL,
                    release_sequence BIGINT NOT NULL,
                    package_sha256 BINARY(32) NOT NULL,
                    package_format_version INT NOT NULL,
                    backend_command_contract_version INT NOT NULL,
                    device_event_contract_version INT NOT NULL,
                    communication_business_protocol_major INT NOT NULL,
                    communication_business_protocol_minor INT NOT NULL,
                    updater_business_protocol_major INT NOT NULL,
                    updater_business_protocol_minor INT NOT NULL,
                    uart_protocol_family VARCHAR(24) NOT NULL,
                    uart_protocol_major INT,
                    uart_protocol_minor INT,
                    required_fixed_frame_revision INT,
                    required_mcu_capability_bitmap_hex VARCHAR(16) NOT NULL
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_device_management_profile (
                    asset_id BIGINT PRIMARY KEY,
                    architecture_generation VARCHAR(24) NOT NULL,
                    transition_source_event_uid VARCHAR(36),
                    transitioned_at TIMESTAMP,
                    lock_version BIGINT NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_device_software_fact (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    event_uid VARCHAR(36) NOT NULL UNIQUE,
                    source_inbox_id BIGINT NOT NULL UNIQUE,
                    asset_id BIGINT NOT NULL,
                    management_state_sequence BIGINT NOT NULL,
                    architecture_generation VARCHAR(24) NOT NULL,
                    business_gate_state VARCHAR(16) NOT NULL,
                    communication_agent_version VARCHAR(64) NOT NULL,
                    management_transport_protocol_major INT NOT NULL,
                    management_transport_protocol_minor INT NOT NULL,
                    communication_business_protocol_major INT NOT NULL,
                    communication_business_protocol_minor INT NOT NULL,
                    communication_updater_protocol_major INT NOT NULL,
                    communication_updater_protocol_minor INT NOT NULL,
                    device_updater_version VARCHAR(64) NOT NULL,
                    device_maintenance_protocol_major INT NOT NULL,
                    device_maintenance_protocol_minor INT NOT NULL,
                    updater_business_protocol_major INT NOT NULL,
                    updater_business_protocol_minor INT NOT NULL,
                    business_package_format_version INT NOT NULL,
                    mcu_package_format_version INT NOT NULL,
                    active_business_release_uid VARCHAR(36),
                    active_business_release_sequence BIGINT,
                    active_business_version_name VARCHAR(64),
                    active_business_package_sha256 BINARY(32),
                    business_process_state VARCHAR(16) NOT NULL,
                    business_process_ready BOOLEAN NOT NULL,
                    negotiated_communication_business_major INT,
                    negotiated_communication_business_minor INT,
                    negotiated_communication_updater_major INT,
                    negotiated_communication_updater_minor INT,
                    negotiated_updater_business_major INT,
                    negotiated_updater_business_minor INT,
                    mcu_firmware_version VARCHAR(64),
                    mcu_firmware_version_code BIGINT,
                    mcu_firmware_identity_hex VARCHAR(16),
                    mcu_fixed_frame_revision INT,
                    uart_state VARCHAR(20) NOT NULL,
                    uart_protocol_family VARCHAR(24) NOT NULL,
                    uart_protocol_major INT,
                    uart_protocol_minor INT,
                    capability_bitmap_hex VARCHAR(16) NOT NULL,
                    payload_sha256 BINARY(32) NOT NULL,
                    normalized_payload CLOB NOT NULL,
                    observed_at TIMESTAMP,
                    received_at TIMESTAMP NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    UNIQUE (asset_id, management_state_sequence)
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_device_compatibility_projection (
                    asset_id BIGINT PRIMARY KEY,
                    architecture_generation VARCHAR(24) NOT NULL,
                    latest_software_fact_id BIGINT,
                    source_event_uid VARCHAR(36),
                    management_state_sequence BIGINT,
                    compatibility_status VARCHAR(24) NOT NULL,
                    business_admission_status VARCHAR(16) NOT NULL,
                    primary_reason_code VARCHAR(64),
                    primary_reason_message VARCHAR(500),
                    reasons_json CLOB NOT NULL,
                    capabilities_json CLOB NOT NULL,
                    observed_at TIMESTAMP,
                    received_at TIMESTAMP,
                    lock_version BIGINT NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_edge_software_deployment (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    asset_id BIGINT NOT NULL,
                    deployment_status VARCHAR(40) NOT NULL
                )
                """);
    }

    private static LocalDateTime now() {
        return LocalDateTime.of(2026, 9, 2, 3, 0);
    }
}
