package org.enveloping.ecobin.device.application.fullness;

import org.enveloping.ecobin.device.api.port.CompleteFullnessSampleDeviceParticipationPort;
import org.enveloping.ecobin.device.api.port.FullnessSampleBusinessWriter;
import org.enveloping.ecobin.device.api.result.FullnessSampleBusinessResult;
import org.enveloping.ecobin.device.api.result.FullnessSampleMeasurement;
import org.enveloping.ecobin.device.api.result.FullnessSamplePersistenceFacts;
import org.enveloping.ecobin.device.api.result.FullnessSamplePhysicalFact;
import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;
import org.enveloping.ecobin.device.application.target.ReliableEdgeConfirmationService;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskProofPort;
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
import java.util.Objects;
import java.util.UUID;

@Service
public class TrustedFullnessSampleCompletionService
        implements CompleteFullnessSampleDeviceParticipationPort {

    private static final String MESSAGE_KIND =
            "FULLNESS_SAMPLE_COMPLETE";
    private static final String TASK_TYPE = "SAMPLE_FULLNESS";
    private static final String TASK_TARGET_TYPE =
            "FULLNESS_SAMPLE";

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final FullnessSampleFactsRefFactory factsRefFactory;
    private final ReliableEdgeConfirmationService confirmationService;
    private final ReliableDeviceTaskProofPort taskProofPort;

    public TrustedFullnessSampleCompletionService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            FullnessSampleFactsRefFactory factsRefFactory,
            ReliableEdgeConfirmationService confirmationService,
            ReliableDeviceTaskProofPort taskProofPort) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.factsRefFactory = factsRefFactory;
        this.confirmationService = confirmationService;
        this.taskProofPort = taskProofPort;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public TrustedDeviceEventApplyResult complete(
            TrustedDeviceInboxEvent event,
            FullnessSampleBusinessWriter businessWriter) {
        if (!MESSAGE_KIND.equals(event.messageKind())
                || event.normalizedSchemaVersion() != 1) {
            throw new IllegalArgumentException(
                    "unsupported fullness completion inbox message");
        }
        FullnessSamplePhysicalFact fact =
                parse(event.normalizedPayload());
        requireAcceptedFixedFrameFact(fact);
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
            FullnessSamplePhysicalFact fact,
            long inboxId,
            long tenantId,
            long organizationId,
            FullnessSampleBusinessWriter businessWriter) {
        long assetId = lockAsset(fact.hardwareSn());
        long deploymentId = lockDeployment(
                fact,
                assetId,
                tenantId,
                organizationId);
        lockDeploymentRuntime(
                deploymentId,
                tenantId,
                organizationId);
        PortRow port = lockPort(
                fact,
                deploymentId,
                tenantId,
                organizationId);
        CommandRow command = lockCommand(
                fact,
                deploymentId,
                tenantId,
                organizationId);
        verifyCommandEnvelope(fact, command);
        verifyConfiguration(
                fact,
                port,
                deploymentId,
                tenantId,
                organizationId);
        if ("PHYSICAL_SUCCEEDED".equals(
                command.physicalState())) {
            return requirePreviouslyApplied(
                    fact,
                    command.id());
        }
        if (!List.of(
                "QUEUED",
                "EDGE_ACCEPTED",
                "PHYSICAL_STARTED")
                .contains(command.physicalState())) {
            throw untrusted(
                    "fullness command is already terminal");
        }
        requireNoEdgeCollision(
                fact,
                inboxId,
                deploymentId);

        LocalDateTime receivedAt = databaseNow();
        long edgeEventId = insertEdgeEvent(
                fact,
                inboxId,
                tenantId,
                organizationId,
                deploymentId,
                receivedAt);
        long physicalResultId = insertPhysicalResult(
                fact,
                port.id(),
                command.id(),
                edgeEventId,
                tenantId,
                organizationId,
                deploymentId,
                receivedAt);
        FullnessSampleBusinessResult business =
                businessWriter.write(
                        factsRefFactory.issue(
                                new FullnessSamplePersistenceFacts(
                                        tenantId,
                                        organizationId,
                                        deploymentId,
                                        port.id(),
                                        command.detectionId(),
                                        command.id(),
                                        edgeEventId,
                                        physicalResultId,
                                        receivedAt,
                                        fact)));

        requireSingle(jdbc.update("""
                        UPDATE dev_device_command
                        SET physical_state = 'PHYSICAL_SUCCEEDED',
                            edge_accepted_at =
                                COALESCE(edge_accepted_at, ?),
                            physical_started_at =
                                COALESCE(physical_started_at, ?),
                            physical_ended_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND physical_state IN (
                              'QUEUED',
                              'EDGE_ACCEPTED',
                              'PHYSICAL_STARTED'
                          )
                        """,
                receivedAt,
                receivedAt,
                receivedAt,
                receivedAt,
                command.id(),
                tenantId,
                organizationId,
                deploymentId),
                "complete fullness sample command");
        taskProofPort.completeFromTrustedProof(
                TASK_TYPE,
                TASK_TARGET_TYPE,
                FullnessSampleCommandService.sampleStableKey(
                        fact.detectionUid(),
                        fact.sampleRole()));
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
                "touch fullness runtime");
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
            throw untrusted(
                    "fullness source asset is unavailable");
        }
        return rows.getFirst();
    }

    private long lockDeployment(
            FullnessSamplePhysicalFact fact,
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
            throw untrusted(
                    "fullness deployment target differs");
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
            throw untrusted(
                    "fullness deployment runtime is missing");
        }
    }

    private PortRow lockPort(
            FullnessSamplePhysicalFact fact,
            long deploymentId,
            long tenantId,
            long organizationId) {
        List<Long> ports = jdbc.query("""
                        SELECT port.id
                        FROM dev_port port
                        WHERE port.tenant_id = ?
                          AND port.organization_id = ?
                          AND port.deployment_id = ?
                          AND port.port_no = ?
                        """,
                (rs, ignored) -> rs.getLong("id"),
                tenantId,
                organizationId,
                deploymentId,
                fact.portNo());
        if (ports.size() != 1) {
            throw untrusted(
                    "fullness port target differs");
        }
        List<PortRow> runtimeRows = jdbc.query("""
                        SELECT port_id
                        FROM dev_port_runtime_state
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND port_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new PortRow(
                        rs.getLong("port_id")),
                tenantId,
                organizationId,
                deploymentId,
                ports.getFirst());
        if (runtimeRows.size() != 1) {
            throw untrusted(
                    "fullness port runtime is missing");
        }
        return runtimeRows.getFirst();
    }

    private CommandRow lockCommand(
            FullnessSamplePhysicalFact fact,
            long deploymentId,
            long tenantId,
            long organizationId) {
        List<CommandRow> rows = jdbc.query("""
                        SELECT id,
                               fullness_detection_id,
                               semantic_payload,
                               physical_state
                        FROM dev_device_command
                        WHERE command_uid = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND command_type =
                              'SAMPLE_FULLNESS'
                        FOR UPDATE
                        """,
                (rs, ignored) -> new CommandRow(
                        rs.getLong("id"),
                        rs.getLong("fullness_detection_id"),
                        rs.getString("semantic_payload"),
                        rs.getString("physical_state")),
                fact.commandUid().toString(),
                tenantId,
                organizationId,
                deploymentId);
        if (rows.size() != 1) {
            throw untrusted(
                    "fullness command target differs");
        }
        return rows.getFirst();
    }

    private void verifyCommandEnvelope(
            FullnessSamplePhysicalFact fact,
            CommandRow command) {
        JsonNode envelope =
                objectMapper.readTree(command.semanticPayload());
        JsonNode target = requiredObject(envelope, "target");
        JsonNode payload = requiredObject(envelope, "payload");
        JsonNode config = requiredObject(payload, "config");
        if (!fact.commandUid().toString().equals(
                requiredText(envelope, "commandUid"))
                || !"SAMPLE_FULLNESS".equals(
                requiredText(envelope, "commandType"))
                || !fact.deploymentCode().equals(
                requiredText(envelope, "deploymentCode"))
                || !"FULLNESS_DETECTION".equals(
                requiredText(target, "type"))
                || !fact.detectionUid().toString().equals(
                requiredText(target, "uid"))
                || !fact.detectionUid().toString().equals(
                requiredText(payload, "detectionUid"))
                || fact.portNo() != positiveInt(payload, "portNo")
                || !fact.sampleRole().equals(
                requiredText(payload, "sampleRole"))
                || !fact.triggerType().equals(
                requiredText(payload, "triggerType"))
                || !fact.fullnessMode().equals(
                requiredText(payload, "fullnessMode"))
                || fact.configurationVersion()
                != positiveLong(config, "version")
                || !fact.configurationContentSha256().equals(
                requiredText(config, "contentSha256"))
                || !fact.configurationMcuPayloadSha256().equals(
                requiredText(config, "mcuPayloadSha256"))) {
            throw untrusted(
                    "fullness result differs from its frozen command");
        }
    }

    private void verifyConfiguration(
            FullnessSamplePhysicalFact fact,
            PortRow port,
            long deploymentId,
            long tenantId,
            long organizationId) {
        List<Long> rows = jdbc.query("""
                        SELECT snapshot.calibration_version
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
                (rs, ignored) ->
                        rs.getLong("calibration_version"),
                port.id(),
                tenantId,
                organizationId,
                deploymentId,
                fact.configurationVersion(),
                digest(fact.configurationContentSha256()),
                digest(fact.configurationMcuPayloadSha256()));
        if (rows.size() != 1
                || rows.getFirst()
                != fact.totalWeightMeasurement()
                .calibrationVersion()) {
            throw untrusted(
                    "fullness result configuration differs");
        }
    }

    private TrustedDeviceEventApplyResult requirePreviouslyApplied(
            FullnessSamplePhysicalFact fact,
            long commandId) {
        List<ExistingResult> rows = jdbc.query("""
                        SELECT edge.event_uid,
                               LOWER(HEX(edge.canonical_sha256))
                                   AS canonical_sha256
                        FROM dev_physical_result result_row
                        JOIN dev_edge_event edge
                          ON edge.id = result_row.edge_event_id
                        WHERE result_row.command_id = ?
                          AND result_row.result_type =
                              'FULLNESS_SAMPLE'
                        """,
                (rs, ignored) -> new ExistingResult(
                        rs.getString("event_uid"),
                        rs.getString("canonical_sha256")),
                commandId);
        if (rows.size() == 1
                && rows.getFirst().eventUid().equals(
                fact.eventUid().toString())
                && rows.getFirst().canonicalSha256().equals(
                fact.canonicalSha256())) {
            return TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED;
        }
        throw untrusted(
                "completed fullness command has another result");
    }

    private void requireNoEdgeCollision(
            FullnessSamplePhysicalFact fact,
            long inboxId,
            long deploymentId) {
        Integer count = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_edge_event
                        WHERE event_uid = ?
                           OR (
                                deployment_id = ?
                                AND edge_event_sequence = ?
                           )
                           OR source_inbox_id = ?
                        """,
                Integer.class,
                fact.eventUid().toString(),
                deploymentId,
                fact.edgeEventSequence(),
                inboxId);
        if (count == null || count != 0) {
            throw untrusted(
                    "fullness event identity or sequence conflicts");
        }
    }

    private long insertEdgeEvent(
            FullnessSamplePhysicalFact fact,
            long inboxId,
            long tenantId,
            long organizationId,
            long deploymentId,
            LocalDateTime receivedAt) {
        requireSingle(jdbc.update("""
                        INSERT INTO dev_edge_event (
                            event_uid,
                            tenant_id, organization_id,
                            deployment_id,
                            edge_event_sequence,
                            event_type, delivery_class,
                            schema_version,
                            target_type,
                            target_stable_key_sha256,
                            device_occurred_at,
                            clock_quality,
                            backend_received_at,
                            payload_sha256,
                            canonical_sha256,
                            source_inbox_id,
                            created_at
                        ) VALUES (
                            ?,
                            ?, ?,
                            ?,
                            ?,
                            'FULLNESS_SAMPLE_COMPLETE',
                            'RELIABLE_FACT',
                            1,
                            'FULLNESS_DETECTION',
                            ?,
                            ?,
                            ?,
                            ?,
                            ?,
                            ?,
                            ?,
                            ?
                        )
                        """,
                fact.eventUid().toString(),
                tenantId,
                organizationId,
                deploymentId,
                fact.edgeEventSequence(),
                sha256(fact.detectionUid().toString()),
                LocalDateTime.ofInstant(
                        fact.deviceOccurredAt(),
                        ZoneOffset.UTC),
                fact.clockQuality(),
                receivedAt,
                digest(fact.payloadSha256()),
                digest(fact.canonicalSha256()),
                inboxId,
                receivedAt),
                "insert fullness edge event");
        Long id = jdbc.queryForObject("""
                        SELECT id
                        FROM dev_edge_event
                        WHERE event_uid = ?
                        """,
                Long.class,
                fact.eventUid().toString());
        if (id == null) {
            throw new IllegalStateException(
                    "fullness edge event id is missing");
        }
        return id;
    }

    private long insertPhysicalResult(
            FullnessSamplePhysicalFact fact,
            long portId,
            long commandId,
            long edgeEventId,
            long tenantId,
            long organizationId,
            long deploymentId,
            LocalDateTime receivedAt) {
        FullnessSampleMeasurement measurement =
                fact.totalWeightMeasurement();
        requireSingle(jdbc.update("""
                        INSERT INTO dev_physical_result (
                            tenant_id, organization_id,
                            deployment_id, port_id,
                            edge_event_id, edge_event_type,
                            command_id, command_type,
                            reported_config_version_no,
                            reported_config_content_sha256,
                            reported_config_mcu_payload_sha256,
                            result_type,
                            fullness_sample_id,
                            fullness_measurement_uid,
                            fullness_measurement_status,
                            fullness_total_weight_g,
                            fullness_last_observed_weight_g,
                            fullness_measurement_elapsed_ms,
                            fullness_sample_count,
                            fullness_calibration_version,
                            fullness_sensor_health,
                            fullness_fault_code,
                            fullness_mcu_boot_id,
                            fullness_mcu_event_sequence,
                            infrared_value,
                            infrared_health,
                            fullness_sensor_kind,
                            fullness_sample_basis,
                            fullness_representative_distance_mm,
                            fullness_requested_sample_count,
                            fullness_valid_sample_count,
                            created_at
                        ) VALUES (
                            ?, ?,
                            ?, ?,
                            ?, 'FULLNESS_SAMPLE_COMPLETE',
                            ?, 'SAMPLE_FULLNESS',
                            ?,
                            ?,
                            ?,
                            'FULLNESS_SAMPLE',
                            NULL,
                            ?,
                            ?,
                            ?,
                            NULL,
                            ?,
                            ?,
                            ?,
                            ?,
                            ?,
                            ?,
                            ?,
                            ?,
                            'OK',
                            ?,
                            ?,
                            ?,
                            ?,
                            ?,
                            ?
                        )
                        """,
                tenantId,
                organizationId,
                deploymentId,
                portId,
                edgeEventId,
                commandId,
                fact.configurationVersion(),
                digest(fact.configurationContentSha256()),
                digest(fact.configurationMcuPayloadSha256()),
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
                fact.fullnessSensorValue(),
                fact.fullnessSensorKind(),
                fact.fullnessSampleBasis(),
                fact.representativeDistanceMm(),
                fact.requestedSampleCount(),
                fact.validSampleCount(),
                receivedAt),
                "insert fullness physical result");
        Long id = jdbc.queryForObject("""
                        SELECT id
                        FROM dev_physical_result
                        WHERE command_id = ?
                        """,
                Long.class,
                commandId);
        if (id == null) {
            throw new IllegalStateException(
                    "fullness physical result id is missing");
        }
        return id;
    }

    private FullnessSamplePhysicalFact parse(
            String normalizedPayload) {
        JsonNode root = objectMapper.readTree(normalizedPayload);
        JsonNode source = requiredObject(root, "trustedSource");
        JsonNode event = requiredObject(root, "event");
        JsonNode target = requiredObject(event, "target");
        JsonNode payload = requiredObject(event, "payload");
        JsonNode frozen = requiredObject(
                payload,
                "frozenConfig");
        if (!MESSAGE_KIND.equals(
                requiredText(event, "eventType"))
                || !"RELIABLE_FACT".equals(
                requiredText(event, "deliveryClass"))
                || !"FULLNESS_DETECTION".equals(
                requiredText(target, "type"))) {
            throw new IllegalArgumentException(
                    "fullness completion envelope constants differ");
        }
        UUID detectionUid = uuid(
                payload,
                "detectionUid");
        if (!detectionUid.toString().equals(
                requiredText(target, "uid"))) {
            throw new IllegalArgumentException(
                    "fullness completion target differs");
        }
        return new FullnessSamplePhysicalFact(
                uuid(event, "eventUid"),
                uuid(event, "commandUid"),
                detectionUid,
                requiredText(source, "deviceName"),
                requiredText(event, "deploymentCode"),
                positiveLong(event, "edgeEventSequence"),
                Instant.parse(requiredText(
                        event,
                        "occurredAt")),
                requiredText(event, "clockQuality"),
                requiredDigest(event, "payloadSha256"),
                requiredDigest(root, "eventCanonicalSha256"),
                positiveInt(payload, "portNo"),
                requiredText(payload, "sampleRole"),
                requiredText(payload, "triggerType"),
                requiredText(payload, "fullnessMode"),
                requiredText(payload, "fullnessSensorKind"),
                requiredText(payload, "fullnessSensorValue"),
                requiredText(payload, "fullnessSampleBasis"),
                nullableLong(
                        payload,
                        "representativeDistanceMm"),
                nonNegativeInt(
                        payload,
                        "requestedSampleCount"),
                nonNegativeInt(
                        payload,
                        "validSampleCount"),
                measurement(requiredObject(
                        payload,
                        "totalWeightMeasurement")),
                positiveLong(frozen, "version"),
                requiredDigest(frozen, "contentSha256"),
                requiredDigest(frozen, "mcuPayloadSha256"));
    }

    private static FullnessSampleMeasurement measurement(
            JsonNode node) {
        return new FullnessSampleMeasurement(
                uuid(node, "measurementUid"),
                requiredText(node, "status"),
                requiredBoolean(
                        node,
                        "weightValueAvailable"),
                nullableLong(
                        node,
                        "reportedWeightGrams"),
                requiredText(node, "weightValueKind"),
                nonNegativeLong(
                        node,
                        "measurementElapsedMs"),
                nonNegativeInt(node, "sampleCount"),
                nonNegativeLong(
                        node,
                        "calibrationVersion"),
                requiredText(node, "sensorHealth"),
                nullableText(node, "faultCode"),
                positiveLong(node, "mcuBootId"),
                positiveLong(
                        node,
                        "mcuEventSequence"));
    }

    private static void requireAcceptedFixedFrameFact(
            FullnessSamplePhysicalFact fact) {
        FullnessSampleMeasurement measurement =
                fact.totalWeightMeasurement();
        boolean roleMatches =
                ("DELIVERY_COMPLETE".equals(
                        fact.triggerType())
                        && List.of(
                        "INITIAL",
                        "CONFIRMATION")
                        .contains(fact.sampleRole()))
                        || ("MANUAL_RECHECK".equals(
                        fact.triggerType())
                        && "MANUAL_RECHECK".equals(
                        fact.sampleRole()));
        if (!roleMatches
                || !List.of(
                "SENSOR_ONLY",
                "WEIGHT_ONLY",
                "SENSOR_OR_WEIGHT")
                .contains(fact.fullnessMode())
                || !"DIGITAL_INFRARED".equals(
                fact.fullnessSensorKind())
                || !List.of("CLEAR", "BLOCKED").contains(
                fact.fullnessSensorValue())
                || !"NOT_SAMPLED".equals(
                fact.fullnessSampleBasis())
                || fact.representativeDistanceMm() != null
                || !((fact.requestedSampleCount() == 1
                && fact.validSampleCount() == 1)
                || (fact.requestedSampleCount() == 0
                && fact.validSampleCount() == 0))
                || !"SYNCED".equals(fact.clockQuality())
                || !"STABLE".equals(measurement.status())
                || !measurement.weightValueAvailable()
                || measurement.reportedWeightGrams() == null
                || !"STABLE_WINDOW_MEAN".equals(
                measurement.weightValueKind())
                || measurement.sampleCount() != 1
                || !"OK".equals(measurement.sensorHealth())
                || measurement.faultCode() != null
                || (fact.requestedSampleCount() == 0
                && measurement.reportedWeightGrams() != 0)) {
            throw new IllegalArgumentException(
                    "only the accepted fixed-frame fullness result is supported");
        }
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

    private static JsonNode requiredObject(
            JsonNode parent,
            String field) {
        JsonNode value = parent == null
                ? null
                : parent.get(field);
        if (value == null || !value.isObject()) {
            throw new IllegalArgumentException(
                    field + " must be an object");
        }
        return value;
    }

    private static String requiredText(
            JsonNode parent,
            String field) {
        JsonNode value = parent == null
                ? null
                : parent.get(field);
        if (value == null
                || !value.isTextual()
                || value.asText().isBlank()) {
            throw new IllegalArgumentException(
                    field + " must not be blank");
        }
        return value.asText();
    }

    private static String nullableText(
            JsonNode parent,
            String field) {
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

    private static UUID uuid(
            JsonNode parent,
            String field) {
        return UUID.fromString(
                requiredText(parent, field));
    }

    private static boolean requiredBoolean(
            JsonNode parent,
            String field) {
        JsonNode value = parent.get(field);
        if (value == null || !value.isBoolean()) {
            throw new IllegalArgumentException(
                    field + " must be boolean");
        }
        return value.asBoolean();
    }

    private static long positiveLong(
            JsonNode parent,
            String field) {
        long value = exactLong(parent, field);
        if (value <= 0) {
            throw new IllegalArgumentException(
                    field + " must be positive");
        }
        return value;
    }

    private static int positiveInt(
            JsonNode parent,
            String field) {
        return Math.toIntExact(
                positiveLong(parent, field));
    }

    private static long nonNegativeLong(
            JsonNode parent,
            String field) {
        long value = exactLong(parent, field);
        if (value < 0) {
            throw new IllegalArgumentException(
                    field + " must be non-negative");
        }
        return value;
    }

    private static int nonNegativeInt(
            JsonNode parent,
            String field) {
        return Math.toIntExact(
                nonNegativeLong(parent, field));
    }

    private static Long nullableLong(
            JsonNode parent,
            String field) {
        JsonNode value = parent.get(field);
        if (value == null || value.isNull()) {
            return null;
        }
        return exactLongValue(value, field);
    }

    private static long exactLong(
            JsonNode parent,
            String field) {
        JsonNode value = parent == null
                ? null
                : parent.get(field);
        return exactLongValue(value, field);
    }

    private static long exactLongValue(
            JsonNode value,
            String field) {
        if (value == null || !value.isNumber()) {
            throw new IllegalArgumentException(
                    field + " must be an integer");
        }
        try {
            return value.decimalValue().longValueExact();
        } catch (ArithmeticException exception) {
            throw new IllegalArgumentException(
                    field + " must be an exact 64-bit integer",
                    exception);
        }
    }

    private static String requiredDigest(
            JsonNode parent,
            String field) {
        String value = requiredText(parent, field);
        if (!value.matches("[0-9a-f]{64}")) {
            throw new IllegalArgumentException(
                    field + " must be lowercase SHA-256");
        }
        return value;
    }

    private static byte[] digest(String value) {
        if (value == null
                || !value.matches("[0-9a-f]{64}")) {
            throw new IllegalArgumentException(
                    "SHA-256 must be lowercase hexadecimal");
        }
        return HexFormat.of().parseHex(value);
    }

    private static byte[] sha256(String value) {
        try {
            return MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException(
                    "SHA-256 is unavailable",
                    exception);
        }
    }

    private static void requireSingle(
            int affected,
            String operation) {
        if (affected != 1) {
            throw new IllegalStateException(
                    operation + " affected " + affected + " rows");
        }
    }

    private static UntrustedInboxSourceException untrusted(
            String detail) {
        return new UntrustedInboxSourceException(detail);
    }

    private record PortRow(long id) {
    }

    private record CommandRow(
            long id,
            long detectionId,
            String semanticPayload,
            String physicalState) {
    }

    private record ExistingResult(
            String eventUid,
            String canonicalSha256) {
    }
}
