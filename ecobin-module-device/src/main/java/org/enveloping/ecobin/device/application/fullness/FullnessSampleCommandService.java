package org.enveloping.ecobin.device.application.fullness;

import org.enveloping.ecobin.device.api.command.ScheduleFullnessSampleCommand;
import org.enveloping.ecobin.device.api.persistence.FullnessDetectionCommandRef;
import org.enveloping.ecobin.device.api.port.ScheduleFullnessSampleDevicePort;
import org.enveloping.ecobin.device.api.result.ScheduledFullnessSample;
import org.enveloping.ecobin.device.application.target.DeviceConfigurationCanonicalizer;
import org.enveloping.ecobin.framework.reliability.DeviceCommandTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskRegistrationPort;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;

@Service
public class FullnessSampleCommandService
        implements ScheduleFullnessSampleDevicePort {

    static final String TASK_TYPE = "SAMPLE_FULLNESS";
    static final String TASK_TARGET_TYPE = "FULLNESS_SAMPLE";
    static final int MAX_AUTO_ATTEMPTS = 1;

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final DeviceConfigurationCanonicalizer canonicalizer;
    private final ReliableDeviceTaskRegistrationPort taskRegistration;
    private final DeviceCommandTaskRefFactory taskRefFactory;

    public FullnessSampleCommandService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            DeviceConfigurationCanonicalizer canonicalizer,
            ReliableDeviceTaskRegistrationPort taskRegistration,
            DeviceCommandTaskRefFactory taskRefFactory) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.canonicalizer = canonicalizer;
        this.taskRegistration = taskRegistration;
        this.taskRefFactory = taskRefFactory;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public ScheduledFullnessSample schedule(
            ScheduleFullnessSampleCommand command) {
        Objects.requireNonNull(command, "command");
        return command.detectionRef().withForeignKeysOnce(
                keys -> scheduleWithinTransaction(command, keys));
    }

    private ScheduledFullnessSample scheduleWithinTransaction(
            ScheduleFullnessSampleCommand command,
            FullnessDetectionCommandRef.ForeignKeys keys) {
        ConfigurationFacts facts =
                loadConfiguration(keys);
        verifyFrozenCommand(command, facts);
        UUID commandUid = UUID.randomUUID();
        LocalDateTime now = databaseNow();
        Instant issuedAt = now.toInstant(ZoneOffset.UTC);
        Instant expiresAt = issuedAt.plusSeconds(60);

        Map<String, Object> payload =
                payload(command, facts);
        byte[] payloadSha256 =
                canonicalizer.payloadSha256(payload);
        Map<String, Object> envelope =
                envelope(
                        command,
                        facts,
                        commandUid,
                        issuedAt,
                        expiresAt,
                        payload,
                        payloadSha256);
        byte[] envelopeSha256 =
                canonicalizer.payloadSha256(envelope);
        String envelopeJson =
                objectMapper.writeValueAsString(envelope);

        int inserted = jdbc.update("""
                        INSERT INTO dev_device_command (
                            command_uid,
                            tenant_id, organization_id,
                            deployment_id,
                            command_type,
                            delivery_session_id,
                            clean_operation_id,
                            config_application_id,
                            fullness_detection_id,
                            baseline_measurement_id,
                            payload_schema_version,
                            semantic_payload,
                            semantic_payload_sha256,
                            physical_state,
                            queued_at,
                            edge_accepted_at,
                            physical_started_at,
                            physical_ended_at,
                            lock_version,
                            created_at, updated_at
                        ) VALUES (
                            ?,
                            ?, ?,
                            ?,
                            'SAMPLE_FULLNESS',
                            NULL, NULL, NULL, ?, NULL,
                            1,
                            CAST(? AS JSON),
                            ?,
                            'QUEUED',
                            ?,
                            NULL, NULL, NULL,
                            0,
                            ?, ?
                        )
                        """,
                commandUid.toString(),
                keys.tenantKey(),
                keys.organizationKey(),
                keys.deploymentKey(),
                keys.detectionKey(),
                envelopeJson,
                envelopeSha256,
                now,
                now,
                now);
        requireSingle(inserted, "insert fullness sample command");
        Long commandId = jdbc.queryForObject("""
                        SELECT id
                        FROM dev_device_command
                        WHERE command_uid = ?
                        """,
                Long.class,
                commandUid.toString());
        if (commandId == null) {
            throw new IllegalStateException(
                    "fullness sample command id is missing");
        }

        String targetStableKey = sampleStableKey(
                command.detectionUid(),
                command.sampleRole());
        Map<String, Object> taskSnapshot = new LinkedHashMap<>();
        taskSnapshot.put("schemaVersion", 1);
        taskSnapshot.put("commandUid", commandUid.toString());
        taskSnapshot.put("commandType", TASK_TYPE);
        taskSnapshot.put("hardwareSn", facts.hardwareSn());
        taskSnapshot.put(
                "deploymentCode",
                facts.deploymentCode());
        taskSnapshot.put(
                "target",
                Map.of(
                        "type",
                        "FULLNESS_DETECTION",
                        "uid",
                        command.detectionUid().toString()));
        taskSnapshot.put(
                "sampleRole",
                command.sampleRole());
        taskSnapshot.put(
                "semanticPayloadSha256",
                canonicalizer.hex(envelopeSha256));
        taskRegistration.register(
                new ReliableDeviceTaskRegistration(
                        TASK_TYPE,
                        TASK_TYPE + ":"
                                + targetStableKey.toUpperCase(
                                Locale.ROOT),
                        TASK_TARGET_TYPE,
                        targetStableKey,
                        taskRefFactory.issue(
                                keys.tenantKey(),
                                keys.organizationKey(),
                                keys.deploymentKey(),
                                commandId),
                        1,
                        objectMapper.writeValueAsString(taskSnapshot),
                        envelopeSha256,
                        command.correlationUid(),
                        command.causationUid(),
                        MAX_AUTO_ATTEMPTS,
                        false,
                        now.plusNanos(
                                command.settleWaitMs()
                                        * 1_000_000L)));
        return new ScheduledFullnessSample(
                commandUid,
                command.detectionUid(),
                command.sampleRole());
    }

    private ConfigurationFacts loadConfiguration(
            FullnessDetectionCommandRef.ForeignKeys keys) {
        List<ConfigurationFacts> rows = jdbc.query("""
                        SELECT deployment.public_code,
                               asset.hardware_sn,
                               port.port_no,
                               version.version_no,
                               LOWER(HEX(version.content_sha256))
                                   AS content_sha256,
                               LOWER(HEX(version.mcu_payload_sha256))
                                   AS mcu_payload_sha256,
                               snapshot.fullness_mode,
                               snapshot.configured_full_weight_g,
                               snapshot.fullness_settle_wait_ms,
                               snapshot.fullness_confirmation_wait_ms,
                               snapshot.weight_measurement_timeout_ms
                        FROM dev_device_deployment deployment
                        JOIN dev_device_asset asset
                          ON asset.id = deployment.asset_id
                        JOIN dev_port port
                          ON port.tenant_id = deployment.tenant_id
                         AND port.organization_id =
                             deployment.organization_id
                         AND port.deployment_id = deployment.id
                         AND port.id = ?
                        JOIN dev_config_version version
                          ON version.tenant_id = deployment.tenant_id
                         AND version.organization_id =
                             deployment.organization_id
                         AND version.deployment_id = deployment.id
                         AND version.id = ?
                        JOIN dev_port_config_snapshot snapshot
                          ON snapshot.tenant_id = deployment.tenant_id
                         AND snapshot.organization_id =
                             deployment.organization_id
                         AND snapshot.deployment_id = deployment.id
                         AND snapshot.config_version_id = version.id
                         AND snapshot.port_id = port.id
                         AND snapshot.id = ?
                        WHERE deployment.tenant_id = ?
                          AND deployment.organization_id = ?
                          AND deployment.id = ?
                        """,
                (rs, ignored) -> new ConfigurationFacts(
                        rs.getString("public_code"),
                        rs.getString("hardware_sn"),
                        rs.getInt("port_no"),
                        rs.getLong("version_no"),
                        rs.getString("content_sha256"),
                        rs.getString("mcu_payload_sha256"),
                        rs.getString("fullness_mode"),
                        rs.getLong("configured_full_weight_g"),
                        rs.getLong("fullness_settle_wait_ms"),
                        rs.getLong("fullness_confirmation_wait_ms"),
                        rs.getLong("weight_measurement_timeout_ms")),
                keys.portKey(),
                keys.deviceConfigVersionKey(),
                keys.portConfigSnapshotKey(),
                keys.tenantKey(),
                keys.organizationKey(),
                keys.deploymentKey());
        if (rows.size() != 1) {
            throw new IllegalStateException(
                    "frozen fullness device configuration is missing");
        }
        return rows.getFirst();
    }

    private static void verifyFrozenCommand(
            ScheduleFullnessSampleCommand command,
            ConfigurationFacts facts) {
        long expectedWait =
                "CONFIRMATION".equals(command.sampleRole())
                        ? facts.confirmationWaitMs()
                        : facts.settleWaitMs();
        if (command.portNo() != facts.portNo()
                || command.configVersion() != facts.configVersion()
                || !command.configContentSha256().equals(
                        facts.contentSha256())
                || !command.configMcuPayloadSha256().equals(
                        facts.mcuPayloadSha256())
                || !command.fullnessMode().equals(
                        facts.fullnessMode())
                || command.configuredFullWeightGrams()
                != facts.configuredFullWeightGrams()
                || command.settleWaitMs() != expectedWait
                || command.measurementTimeoutMs()
                != facts.measurementTimeoutMs()) {
            throw new IllegalStateException(
                    "fullness sample command differs from frozen device facts");
        }
    }

    private static Map<String, Object> payload(
            ScheduleFullnessSampleCommand command,
            ConfigurationFacts facts) {
        Map<String, Object> config = new LinkedHashMap<>();
        config.put("version", command.configVersion());
        config.put(
                "contentSha256",
                command.configContentSha256());
        config.put(
                "mcuPayloadSha256",
                command.configMcuPayloadSha256());

        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put(
                "detectionUid",
                command.detectionUid().toString());
        payload.put("portNo", command.portNo());
        payload.put("sampleRole", command.sampleRole());
        payload.put("triggerType", command.triggerType());
        payload.put(
                "fullnessMode",
                machineFullnessMode(facts.fullnessMode()));
        payload.put(
                "currentBaselineWeightGrams",
                command.currentBaselineWeightGrams());
        payload.put(
                "configuredFullWeightGrams",
                command.configuredFullWeightGrams());
        payload.put("settleWaitMs", command.settleWaitMs());
        payload.put(
                "measurementTimeoutMs",
                command.measurementTimeoutMs());
        payload.put("config", config);
        return payload;
    }

    private static Map<String, Object> envelope(
            ScheduleFullnessSampleCommand command,
            ConfigurationFacts facts,
            UUID commandUid,
            Instant issuedAt,
            Instant expiresAt,
            Map<String, Object> payload,
            byte[] payloadSha256) {
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("schemaVersion", 1);
        envelope.put("commandUid", commandUid.toString());
        envelope.put("commandType", TASK_TYPE);
        envelope.put(
                "deploymentCode",
                facts.deploymentCode());
        envelope.put(
                "target",
                Map.of(
                        "type",
                        "FULLNESS_DETECTION",
                        "uid",
                        command.detectionUid().toString()));
        envelope.put("issuedAt", issuedAt.toString());
        envelope.put("expiresAt", expiresAt.toString());
        envelope.put("payloadSchemaVersion", 1);
        envelope.put(
                "payloadSha256",
                HexFormat.of().formatHex(payloadSha256));
        envelope.put("payload", payload);
        envelope.put("cosGrant", null);
        return envelope;
    }

    public static String sampleStableKey(
            UUID detectionUid,
            String sampleRole) {
        return detectionUid + ":" + sampleRole;
    }

    private LocalDateTime databaseNow() {
        LocalDateTime now = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)",
                LocalDateTime.class);
        if (now == null) {
            throw new IllegalStateException(
                    "database time is unavailable");
        }
        return now;
    }

    private static String machineFullnessMode(String value) {
        return switch (value) {
            case "INFRARED_ONLY" -> "SENSOR_ONLY";
            case "WEIGHT_ONLY" -> "WEIGHT_ONLY";
            case "INFRARED_OR_WEIGHT" -> "SENSOR_OR_WEIGHT";
            default -> throw new IllegalArgumentException(
                    "unsupported fullness mode");
        };
    }

    private static void requireSingle(
            int affected,
            String operation) {
        if (affected != 1) {
            throw new IllegalStateException(
                    operation + " affected " + affected + " rows");
        }
    }

    private record ConfigurationFacts(
            String deploymentCode,
            String hardwareSn,
            int portNo,
            long configVersion,
            String contentSha256,
            String mcuPayloadSha256,
            String fullnessMode,
            long configuredFullWeightGrams,
            long settleWaitMs,
            long confirmationWaitMs,
            long measurementTimeoutMs) {
    }
}
