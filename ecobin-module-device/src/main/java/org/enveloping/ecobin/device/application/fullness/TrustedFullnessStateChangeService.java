package org.enveloping.ecobin.device.application.fullness;

import org.enveloping.ecobin.device.api.persistence.DeviceOwnedFullnessStateChangeFactsRefFactory;
import org.enveloping.ecobin.device.api.port.CompleteFullnessStateChangeDeviceParticipationPort;
import org.enveloping.ecobin.device.api.port.FullnessStateChangeBusinessWriter;
import org.enveloping.ecobin.device.api.port.TrustedDeviceTransportPresencePort;
import org.enveloping.ecobin.device.api.result.FullnessSampleMeasurement;
import org.enveloping.ecobin.device.api.result.FullnessStateChangeBusinessResult;
import org.enveloping.ecobin.device.api.result.FullnessStateChangePersistenceFacts;
import org.enveloping.ecobin.device.api.result.FullnessStateChangePhysicalFact;
import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;
import org.enveloping.ecobin.device.application.target.ReliableEdgeConfirmationService;
import org.enveloping.ecobin.framework.reliability.UntrustedInboxSourceException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.HexFormat;
import java.util.List;
import java.util.Set;
import java.util.UUID;

/**
 * Persists a commandless edge-owned fullness transition before recycling
 * projects it onto the current bag. No downlink command is required or
 * inferred from this fact.
 */
@Service
public class TrustedFullnessStateChangeService
        implements CompleteFullnessStateChangeDeviceParticipationPort {

    private static final String MESSAGE_KIND =
            "FULLNESS_STATE_CHANGED";

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final DeviceOwnedFullnessStateChangeFactsRefFactory factsFactory;
    private final ReliableEdgeConfirmationService confirmationService;
    private final TrustedDeviceTransportPresencePort transportPresence;

    public TrustedFullnessStateChangeService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            DeviceOwnedFullnessStateChangeFactsRefFactory factsFactory,
            ReliableEdgeConfirmationService confirmationService,
            TrustedDeviceTransportPresencePort transportPresence) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.factsFactory = factsFactory;
        this.confirmationService = confirmationService;
        this.transportPresence = transportPresence;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public TrustedDeviceEventApplyResult complete(
            TrustedDeviceInboxEvent event,
            FullnessStateChangeBusinessWriter businessWriter) {
        if (!MESSAGE_KIND.equals(event.messageKind())
                || event.normalizedSchemaVersion() != 1) {
            throw new IllegalArgumentException(
                    "unsupported fullness state inbox message");
        }
        FullnessStateChangePhysicalFact fact =
                parse(event.normalizedPayload());
        validate(fact);
        return event.sourceInbox().use(
                (inboxId, tenantId, organizationId) ->
                        completeWithinScope(
                                fact,
                                inboxId,
                                tenantId,
                                organizationId,
                                businessWriter));
    }

    private TrustedDeviceEventApplyResult completeWithinScope(
            FullnessStateChangePhysicalFact fact,
            long inboxId,
            long tenantId,
            long organizationId,
            FullnessStateChangeBusinessWriter businessWriter) {
        long assetId = lockAsset(fact.hardwareSn());
        long deploymentId = lockDeployment(
                fact, assetId, tenantId, organizationId);
        transportPresence.observeAuthenticatedMessage(
                fact.hardwareSn(), inboxId);
        lockDeploymentRuntime(
                deploymentId, tenantId, organizationId);
        long portId = lockPort(
                fact, deploymentId, tenantId, organizationId);
        verifyConfiguration(
                fact,
                portId,
                deploymentId,
                tenantId,
                organizationId);

        TrustedDeviceEventApplyResult duplicate =
                previouslyApplied(fact);
        if (duplicate != null) {
            return duplicate;
        }
        requireNoEdgeCollision(fact, inboxId, deploymentId);
        LocalDateTime receivedAt = databaseNow();
        long edgeEventId = insertEdgeEvent(
                fact,
                inboxId,
                tenantId,
                organizationId,
                deploymentId,
                receivedAt);
        long stateFactId = insertStateFact(
                fact,
                tenantId,
                organizationId,
                deploymentId,
                portId,
                edgeEventId,
                receivedAt);
        Long sourceDeliverySessionId = sourceDeliverySessionId(
                fact,
                tenantId,
                organizationId,
                deploymentId,
                portId);
        FullnessStateChangeBusinessResult business =
                businessWriter.write(factsFactory.issue(
                        new FullnessStateChangePersistenceFacts(
                                tenantId,
                                organizationId,
                                deploymentId,
                                portId,
                                edgeEventId,
                                stateFactId,
                                sourceDeliverySessionId,
                                receivedAt,
                                fact)));
        requireSingle(jdbc.update("""
                        UPDATE dev_deployment_runtime_state
                        SET last_device_event_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE deployment_id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                        """,
                receivedAt,
                receivedAt,
                deploymentId,
                tenantId,
                organizationId),
                "touch fullness state runtime");
        confirmationService.registerApplied(
                tenantId,
                organizationId,
                deploymentId,
                fact.deploymentCode(),
                fact.eventUid().toString(),
                fact.payloadSha256(),
                "UPDATED",
                business.resultReferences(),
                receivedAt);
        return TrustedDeviceEventApplyResult.APPLIED;
    }

    private long lockAsset(String hardwareSn) {
        List<Long> rows = jdbc.query("""
                        SELECT id
                        FROM dev_device_asset
                        WHERE hardware_sn = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("id"),
                hardwareSn);
        if (rows.size() != 1) {
            throw untrusted("fullness state source asset is unavailable");
        }
        return rows.getFirst();
    }

    private long lockDeployment(
            FullnessStateChangePhysicalFact fact,
            long assetId,
            long tenantId,
            long organizationId) {
        List<Long> rows = jdbc.query("""
                        SELECT deployment.id
                        FROM dev_asset_active_deployment active
                        JOIN dev_device_deployment deployment
                          ON deployment.id = active.deployment_id
                         AND deployment.tenant_id = active.tenant_id
                         AND deployment.organization_id =
                             active.organization_id
                        WHERE active.asset_id = ?
                          AND active.tenant_id = ?
                          AND active.organization_id = ?
                          AND deployment.public_code = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("id"),
                assetId,
                tenantId,
                organizationId,
                fact.deploymentCode());
        if (rows.size() != 1) {
            throw untrusted("fullness state deployment target differs");
        }
        return rows.getFirst();
    }

    private void lockDeploymentRuntime(
            long deploymentId,
            long tenantId,
            long organizationId) {
        List<Long> rows = jdbc.query("""
                        SELECT deployment_id
                        FROM dev_deployment_runtime_state
                        WHERE deployment_id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("deployment_id"),
                deploymentId,
                tenantId,
                organizationId);
        if (rows.size() != 1) {
            throw untrusted("fullness state runtime is missing");
        }
    }

    private long lockPort(
            FullnessStateChangePhysicalFact fact,
            long deploymentId,
            long tenantId,
            long organizationId) {
        List<Long> rows = jdbc.query("""
                        SELECT port.id
                        FROM dev_port port
                        JOIN dev_port_runtime_state runtime
                          ON runtime.tenant_id = port.tenant_id
                         AND runtime.organization_id = port.organization_id
                         AND runtime.deployment_id = port.deployment_id
                         AND runtime.port_id = port.id
                        WHERE port.tenant_id = ?
                          AND port.organization_id = ?
                          AND port.deployment_id = ?
                          AND port.port_no = ?
                        FOR UPDATE OF runtime
                        """,
                (rs, ignored) -> rs.getLong("id"),
                tenantId,
                organizationId,
                deploymentId,
                fact.portNo());
        if (rows.size() != 1) {
            throw untrusted("fullness state port target differs");
        }
        return rows.getFirst();
    }

    private void verifyConfiguration(
            FullnessStateChangePhysicalFact fact,
            long portId,
            long deploymentId,
            long tenantId,
            long organizationId) {
        List<ConfigRow> rows = jdbc.query("""
                        SELECT snapshot.fullness_mode,
                               snapshot.configured_full_weight_g,
                               snapshot.calibration_version
                        FROM dev_config_version version
                        JOIN dev_port_config_snapshot snapshot
                          ON snapshot.tenant_id = version.tenant_id
                         AND snapshot.organization_id =
                             version.organization_id
                         AND snapshot.deployment_id =
                             version.deployment_id
                         AND snapshot.config_version_id = version.id
                         AND snapshot.port_id = ?
                        WHERE version.tenant_id = ?
                          AND version.organization_id = ?
                          AND version.deployment_id = ?
                          AND version.version_no = ?
                          AND version.content_sha256 = ?
                          AND version.mcu_payload_sha256 = ?
                        """,
                (rs, ignored) -> new ConfigRow(
                        machineMode(rs.getString("fullness_mode")),
                        rs.getLong("configured_full_weight_g"),
                        rs.getLong("calibration_version")),
                portId,
                tenantId,
                organizationId,
                deploymentId,
                fact.configurationVersion(),
                digest(fact.configurationContentSha256()),
                digest(fact.configurationMcuPayloadSha256()));
        if (rows.size() != 1) {
            throw untrusted("fullness state configuration is unavailable");
        }
        ConfigRow config = rows.getFirst();
        if (!config.mode().equals(fact.fullnessMode())
                || config.fullWeightGrams()
                != fact.configuredFullWeightGrams()
                || config.calibrationVersion()
                != fact.totalWeightMeasurement().calibrationVersion()) {
            throw untrusted("fullness state configuration differs");
        }
    }

    private TrustedDeviceEventApplyResult previouslyApplied(
            FullnessStateChangePhysicalFact fact) {
        List<ExistingFact> rows = jdbc.query("""
                        SELECT state_fact.state_change_uid,
                               edge.event_uid,
                               LOWER(HEX(edge.canonical_sha256))
                                   AS canonical_sha256
                        FROM dev_fullness_state_fact state_fact
                        JOIN dev_edge_event edge
                          ON edge.id = state_fact.edge_event_id
                        WHERE state_fact.state_change_uid = ?
                           OR edge.event_uid = ?
                        """,
                (rs, ignored) -> new ExistingFact(
                        rs.getString("state_change_uid"),
                        rs.getString("event_uid"),
                        rs.getString("canonical_sha256")),
                fact.stateChangeUid().toString(),
                fact.eventUid().toString());
        if (rows.isEmpty()) {
            return null;
        }
        if (rows.size() == 1
                && rows.getFirst().matches(fact)) {
            return TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED;
        }
        throw untrusted("fullness state identity conflicts");
    }

    private void requireNoEdgeCollision(
            FullnessStateChangePhysicalFact fact,
            long inboxId,
            long deploymentId) {
        Integer count = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_edge_event
                        WHERE event_uid = ?
                           OR (deployment_id = ?
                               AND edge_event_sequence = ?)
                           OR source_inbox_id = ?
                        """,
                Integer.class,
                fact.eventUid().toString(),
                deploymentId,
                fact.edgeEventSequence(),
                inboxId);
        if (count == null || count != 0) {
            throw untrusted("fullness state event sequence conflicts");
        }
    }

    private long insertEdgeEvent(
            FullnessStateChangePhysicalFact fact,
            long inboxId,
            long tenantId,
            long organizationId,
            long deploymentId,
            LocalDateTime receivedAt) {
        requireSingle(jdbc.update("""
                        INSERT INTO dev_edge_event (
                            event_uid,
                            tenant_id, organization_id, deployment_id,
                            edge_event_sequence,
                            event_type, delivery_class, schema_version,
                            target_type, target_stable_key_sha256,
                            device_occurred_at, clock_quality,
                            backend_received_at,
                            payload_sha256, canonical_sha256,
                            source_inbox_id, created_at
                        ) VALUES (
                            ?, ?, ?, ?, ?,
                            'FULLNESS_STATE_CHANGED',
                            'RELIABLE_FACT', 1,
                            'PORT_FULLNESS_STATE', ?,
                            ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                fact.eventUid().toString(),
                tenantId,
                organizationId,
                deploymentId,
                fact.edgeEventSequence(),
                sha256(fact.stateChangeUid().toString()),
                LocalDateTime.ofInstant(
                        fact.deviceOccurredAt(), ZoneOffset.UTC),
                fact.clockQuality(),
                receivedAt,
                digest(fact.payloadSha256()),
                digest(fact.canonicalSha256()),
                inboxId,
                receivedAt),
                "insert fullness state edge event");
        Long id = jdbc.queryForObject("""
                        SELECT id FROM dev_edge_event
                        WHERE event_uid = ?
                        """,
                Long.class,
                fact.eventUid().toString());
        if (id == null) {
            throw new IllegalStateException(
                    "fullness state edge event id is missing");
        }
        return id;
    }

    private long insertStateFact(
            FullnessStateChangePhysicalFact fact,
            long tenantId,
            long organizationId,
            long deploymentId,
            long portId,
            long edgeEventId,
            LocalDateTime receivedAt) {
        FullnessSampleMeasurement measurement =
                fact.totalWeightMeasurement();
        requireSingle(jdbc.update("""
                        INSERT INTO dev_fullness_state_fact (
                            state_change_uid,
                            tenant_id, organization_id,
                            deployment_id, port_id,
                            edge_event_id, edge_event_sequence,
                            bag_uid, reported_state,
                            source_work_type, source_work_uid,
                            fullness_mode,
                            fullness_sensor_kind,
                            fullness_sensor_value,
                            confirmation_basis,
                            measurement_uid, measurement_status,
                            total_weight_g,
                            measurement_elapsed_ms, sample_count,
                            calibration_version, sensor_health,
                            fault_code, mcu_boot_id, mcu_event_sequence,
                            baseline_weight_g,
                            configured_full_weight_g,
                            fullness_percent_hundredths,
                            weight_full,
                            reported_config_version_no,
                            reported_config_content_sha256,
                            reported_config_mcu_payload_sha256,
                            device_occurred_at,
                            backend_received_at, created_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                fact.stateChangeUid().toString(),
                tenantId,
                organizationId,
                deploymentId,
                portId,
                edgeEventId,
                fact.edgeEventSequence(),
                fact.bagUid().toString(),
                fact.state(),
                fact.sourceWorkType(),
                fact.sourceWorkUid().toString(),
                fact.fullnessMode(),
                fact.fullnessSensorKind(),
                fact.fullnessSensorValue(),
                fact.confirmationBasis(),
                measurement.measurementUid().toString(),
                measurement.status(),
                measurement.reportedWeightGrams(),
                measurement.measurementElapsedMs(),
                measurement.sampleCount(),
                measurement.calibrationVersion(),
                measurement.sensorHealth(),
                measurement.faultCode(),
                measurement.mcuBootId(),
                measurement.mcuEventSequence(),
                fact.baselineWeightGrams(),
                fact.configuredFullWeightGrams(),
                fact.fullnessPercentHundredths(),
                fact.weightFull(),
                fact.configurationVersion(),
                digest(fact.configurationContentSha256()),
                digest(fact.configurationMcuPayloadSha256()),
                LocalDateTime.ofInstant(
                        fact.deviceOccurredAt(), ZoneOffset.UTC),
                receivedAt,
                receivedAt),
                "insert fullness state physical fact");
        Long id = jdbc.queryForObject("""
                        SELECT id FROM dev_fullness_state_fact
                        WHERE state_change_uid = ?
                        """,
                Long.class,
                fact.stateChangeUid().toString());
        if (id == null) {
            throw new IllegalStateException(
                    "fullness state fact id is missing");
        }
        return id;
    }

    private Long sourceDeliverySessionId(
            FullnessStateChangePhysicalFact fact,
            long tenantId,
            long organizationId,
            long deploymentId,
            long portId) {
        if (!"DELIVERY_SESSION".equals(fact.sourceWorkType())) {
            return null;
        }
        List<Long> rows = jdbc.query("""
                        SELECT id
                        FROM dev_delivery_session
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND port_id = ?
                          AND session_uid = ?
                        """,
                (rs, ignored) -> rs.getLong("id"),
                tenantId,
                organizationId,
                deploymentId,
                portId,
                fact.sourceWorkUid().toString());
        if (rows.size() != 1) {
            throw untrusted(
                    "fullness state delivery source is unavailable");
        }
        return rows.getFirst();
    }

    private FullnessStateChangePhysicalFact parse(
            String normalizedPayload) {
        JsonNode root = objectMapper.readTree(normalizedPayload);
        JsonNode source = requiredObject(root, "trustedSource");
        JsonNode event = requiredObject(root, "event");
        JsonNode target = requiredObject(event, "target");
        JsonNode payload = requiredObject(event, "payload");
        JsonNode frozen = requiredObject(payload, "frozenConfig");
        if (!MESSAGE_KIND.equals(requiredText(event, "eventType"))
                || !"RELIABLE_FACT".equals(
                requiredText(event, "deliveryClass"))
                || !"PORT_FULLNESS_STATE".equals(
                requiredText(target, "type"))
                || !requiredText(payload, "stateChangeUid").equals(
                requiredText(target, "uid"))
                || !event.get("commandUid").isNull()) {
            throw new IllegalArgumentException(
                    "fullness state envelope constants differ");
        }
        JsonNode measurement = requiredObject(
                payload, "totalWeightMeasurement");
        return new FullnessStateChangePhysicalFact(
                uuid(event, "eventUid"),
                uuid(payload, "stateChangeUid"),
                requiredText(source, "deviceName"),
                requiredText(event, "deploymentCode"),
                positiveLong(event, "edgeEventSequence"),
                Instant.parse(requiredText(event, "occurredAt")),
                requiredText(event, "clockQuality"),
                requiredDigest(event, "payloadSha256"),
                requiredDigest(root, "eventCanonicalSha256"),
                positiveInt(payload, "portNo"),
                uuid(payload, "bagUid"),
                requiredText(payload, "state"),
                requiredText(payload, "sourceWorkType"),
                uuid(payload, "sourceWorkUid"),
                requiredText(payload, "fullnessMode"),
                requiredText(payload, "fullnessSensorKind"),
                requiredText(payload, "fullnessSensorValue"),
                requiredText(payload, "confirmationBasis"),
                measurement(measurement),
                nullableLong(payload, "baselineWeightGrams"),
                positiveLong(payload, "configuredFullWeightGrams"),
                nullableLong(payload, "fullnessPercentHundredths"),
                nullableBoolean(payload, "weightFull"),
                positiveLong(frozen, "version"),
                requiredDigest(frozen, "contentSha256"),
                requiredDigest(frozen, "mcuPayloadSha256"));
    }

    private static FullnessSampleMeasurement measurement(JsonNode node) {
        return new FullnessSampleMeasurement(
                uuid(node, "measurementUid"),
                requiredText(node, "status"),
                requiredBoolean(node, "weightValueAvailable"),
                nullableLong(node, "reportedWeightGrams"),
                requiredText(node, "weightValueKind"),
                nonNegativeLong(node, "measurementElapsedMs"),
                nonNegativeInt(node, "sampleCount"),
                nonNegativeLong(node, "calibrationVersion"),
                requiredText(node, "sensorHealth"),
                nullableText(node, "faultCode"),
                positiveLong(node, "mcuBootId"),
                positiveLong(node, "mcuEventSequence"));
    }

    static void validate(FullnessStateChangePhysicalFact fact) {
        FullnessSampleMeasurement measurement =
                fact.totalWeightMeasurement();
        if (!Set.of("FULL", "NOT_FULL").contains(fact.state())
                || !Set.of("DELIVERY_SESSION", "CLEAN_OPERATION")
                .contains(fact.sourceWorkType())
                || !Set.of("SENSOR_ONLY", "WEIGHT_ONLY",
                "SENSOR_OR_WEIGHT").contains(fact.fullnessMode())
                || !Set.of("ULTRASONIC", "DIGITAL_INFRARED")
                .contains(fact.fullnessSensorKind())
                || !Set.of("CLEAR", "BLOCKED", "NOT_SAMPLED")
                .contains(fact.fullnessSensorValue())
                || !Set.of("FIXED_FRAME_CACHED_FINAL_OBSERVATION",
                "MCU_INDEPENDENT_RECHECK")
                .contains(fact.confirmationBasis())
                || !"SYNCED".equals(fact.clockQuality())
                || fact.portNo() < 1 || fact.portNo() > 6
                || !"STABLE".equals(measurement.status())
                || !measurement.weightValueAvailable()
                || measurement.reportedWeightGrams() == null
                || !"STABLE_WINDOW_MEAN".equals(
                measurement.weightValueKind())
                || measurement.sampleCount() < 1
                || !"OK".equals(measurement.sensorHealth())
                || measurement.faultCode() != null) {
            throw new IllegalArgumentException(
                    "fullness state evidence is invalid");
        }

        Boolean calculatedWeightFull = null;
        Long calculatedPercent = null;
        if (fact.baselineWeightGrams() != null) {
            long net = Math.max(0L, Math.subtractExact(
                    measurement.reportedWeightGrams(),
                    fact.baselineWeightGrams()));
            calculatedWeightFull =
                    net >= fact.configuredFullWeightGrams();
            calculatedPercent = Math.multiplyExact(net, 10_000L)
                    / fact.configuredFullWeightGrams();
        }
        if (!java.util.Objects.equals(
                calculatedWeightFull, fact.weightFull())
                || !java.util.Objects.equals(
                calculatedPercent,
                fact.fullnessPercentHundredths())) {
            throw new IllegalArgumentException(
                    "fullness state weight evidence differs");
        }
        boolean sensorFull =
                "BLOCKED".equals(fact.fullnessSensorValue());
        boolean sensorClear =
                "CLEAR".equals(fact.fullnessSensorValue());
        boolean decisionMatches = switch (fact.fullnessMode()) {
            case "SENSOR_ONLY" -> sensorClear || sensorFull
                    ? fact.state().equals(
                    sensorFull ? "FULL" : "NOT_FULL")
                    : false;
            case "WEIGHT_ONLY" -> fact.weightFull() != null
                    && fact.state().equals(
                    fact.weightFull() ? "FULL" : "NOT_FULL");
            case "SENSOR_OR_WEIGHT" ->
                    "FULL".equals(fact.state())
                            ? sensorFull
                            || Boolean.TRUE.equals(fact.weightFull())
                            : sensorClear
                            && Boolean.FALSE.equals(fact.weightFull());
            default -> false;
        };
        if (!decisionMatches) {
            throw new IllegalArgumentException(
                    "fullness state decision differs from evidence");
        }
    }

    private LocalDateTime databaseNow() {
        LocalDateTime now = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        if (now == null) {
            throw new IllegalStateException(
                    "database time is unavailable");
        }
        return now;
    }

    private static JsonNode requiredObject(
            JsonNode parent, String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isObject()) {
            throw new IllegalArgumentException(
                    field + " must be an object");
        }
        return value;
    }

    private static String requiredText(
            JsonNode parent, String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isTextual()
                || value.asText().isBlank()) {
            throw new IllegalArgumentException(
                    field + " must be text");
        }
        return value.asText();
    }

    private static String nullableText(
            JsonNode parent, String field) {
        JsonNode value = parent.get(field);
        if (value == null || value.isNull()) {
            return null;
        }
        if (!value.isTextual()) {
            throw new IllegalArgumentException(
                    field + " must be nullable text");
        }
        return value.asText();
    }

    private static String requiredDigest(
            JsonNode parent, String field) {
        String value = requiredText(parent, field);
        if (!value.matches("[0-9a-f]{64}")) {
            throw new IllegalArgumentException(
                    field + " must be SHA-256");
        }
        return value;
    }

    private static UUID uuid(JsonNode parent, String field) {
        return UUID.fromString(requiredText(parent, field));
    }

    private static long positiveLong(
            JsonNode parent, String field) {
        JsonNode value = parent.get(field);
        if (value == null || !value.canConvertToLong()
                || value.asLong() <= 0) {
            throw new IllegalArgumentException(
                    field + " must be positive");
        }
        return value.asLong();
    }

    private static int positiveInt(
            JsonNode parent, String field) {
        long value = positiveLong(parent, field);
        if (value > Integer.MAX_VALUE) {
            throw new IllegalArgumentException(
                    field + " is too large");
        }
        return (int) value;
    }

    private static long nonNegativeLong(
            JsonNode parent, String field) {
        JsonNode value = parent.get(field);
        if (value == null || !value.canConvertToLong()
                || value.asLong() < 0) {
            throw new IllegalArgumentException(
                    field + " must be non-negative");
        }
        return value.asLong();
    }

    private static int nonNegativeInt(
            JsonNode parent, String field) {
        long value = nonNegativeLong(parent, field);
        if (value > Integer.MAX_VALUE) {
            throw new IllegalArgumentException(
                    field + " is too large");
        }
        return (int) value;
    }

    private static Long nullableLong(
            JsonNode parent, String field) {
        JsonNode value = parent.get(field);
        if (value == null || value.isNull()) {
            return null;
        }
        if (!value.canConvertToLong()) {
            throw new IllegalArgumentException(
                    field + " must be nullable integer");
        }
        return value.asLong();
    }

    private static boolean requiredBoolean(
            JsonNode parent, String field) {
        JsonNode value = parent.get(field);
        if (value == null || !value.isBoolean()) {
            throw new IllegalArgumentException(
                    field + " must be boolean");
        }
        return value.asBoolean();
    }

    private static Boolean nullableBoolean(
            JsonNode parent, String field) {
        JsonNode value = parent.get(field);
        if (value == null || value.isNull()) {
            return null;
        }
        if (!value.isBoolean()) {
            throw new IllegalArgumentException(
                    field + " must be nullable boolean");
        }
        return value.asBoolean();
    }

    private static String machineMode(String stored) {
        return switch (stored) {
            case "INFRARED_ONLY" -> "SENSOR_ONLY";
            case "WEIGHT_ONLY" -> "WEIGHT_ONLY";
            case "INFRARED_OR_WEIGHT" -> "SENSOR_OR_WEIGHT";
            default -> throw untrusted(
                    "unsupported fullness mode");
        };
    }

    private static byte[] digest(String hex) {
        if (hex == null || !hex.matches("[0-9a-f]{64}")) {
            throw new IllegalArgumentException(
                    "SHA-256 must be lowercase hexadecimal");
        }
        return HexFormat.of().parseHex(hex);
    }

    private static byte[] sha256(String value) {
        try {
            return MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException(
                    "JVM does not provide SHA-256", exception);
        }
    }

    private static void requireSingle(int affected, String operation) {
        if (affected != 1) {
            throw new IllegalStateException(
                    operation + " affected " + affected + " rows");
        }
    }

    private static UntrustedInboxSourceException untrusted(String detail) {
        return new UntrustedInboxSourceException(detail);
    }

    private record ConfigRow(
            String mode,
            long fullWeightGrams,
            long calibrationVersion) {
    }

    private record ExistingFact(
            String stateChangeUid,
            String eventUid,
            String canonicalSha256) {

        private boolean matches(FullnessStateChangePhysicalFact fact) {
            return stateChangeUid.equals(
                    fact.stateChangeUid().toString())
                    && eventUid.equals(fact.eventUid().toString())
                    && canonicalSha256.equals(fact.canonicalSha256());
        }
    }
}
