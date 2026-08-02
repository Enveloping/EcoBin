package org.enveloping.ecobin.device.application.delivery;

import org.enveloping.ecobin.device.api.port.CompleteDeliveryDeviceParticipationPort;
import org.enveloping.ecobin.device.api.port.DeliveryCompletionBusinessWriter;
import org.enveloping.ecobin.device.api.port.TrustedDeviceTransportPresencePort;
import org.enveloping.ecobin.device.api.result.DeliveryCompleteDoorCommand;
import org.enveloping.ecobin.device.api.result.DeliveryCompleteMeasurement;
import org.enveloping.ecobin.device.api.result.DeliveryCompletePhoto;
import org.enveloping.ecobin.device.api.result.DeliveryCompletePhysicalFact;
import org.enveloping.ecobin.device.api.result.DeliveryCompletionBusinessResult;
import org.enveloping.ecobin.device.api.result.DeliveryCompletionPersistenceFacts;
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

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Set;
import java.util.UUID;

/**
 * Persists the trusted Orange Pi's single normal delivery completion and
 * invokes recycling before releasing the device occupancy.
 *
 * <p>This slice intentionally supports the normal stable-weight completion.
 * Timeout recovery, interrupted sessions and physical recovery remain outside
 * the current implementation.</p>
 */
@Service
public class TrustedDeliveryCompletionService
        implements CompleteDeliveryDeviceParticipationPort {

    private static final String MESSAGE_KIND = "DELIVERY_COMPLETE";
    private static final String TASK_TYPE = "START_DELIVERY_SESSION";
    private static final String TARGET_TYPE = "DELIVERY_SESSION";

    static final String FIND_EDGE_COLLISIONS_SQL = """
            SELECT id, event_uid, deployment_id,
                   edge_event_sequence,
                   LOWER(HEX(canonical_sha256))
                       AS canonical_sha256,
                   source_inbox_id
            FROM dev_edge_event
            WHERE event_uid = ?
               OR (
                    deployment_id = ?
                    AND edge_event_sequence = ?
               )
               OR source_inbox_id = ?
            """;

    static final String LOCK_PORT_RUNTIME_SQL = """
            SELECT port_id
            FROM dev_port_runtime_state
            WHERE port_id = ?
              AND tenant_id = ?
              AND organization_id = ?
              AND deployment_id = ?
            FOR UPDATE
            """;

    static final String LOAD_PORT_SQL = """
            SELECT id
            FROM dev_port
            WHERE id = ?
              AND tenant_id = ?
              AND organization_id = ?
              AND deployment_id = ?
            """;

    static final String LOAD_PORT_CONFIGURATION_SQL = """
            SELECT port.port_no,
                   snapshot.fullness_mode,
                   snapshot.configured_full_weight_g,
                   snapshot.fullness_settle_wait_ms,
                   snapshot.fullness_confirmation_wait_ms,
                   snapshot.weight_measurement_timeout_ms,
                   snapshot.weight_required_sample_count,
                   snapshot.weight_minimum_g,
                   snapshot.weight_maximum_g,
                   snapshot.calibration_version
            FROM dev_port_config_snapshot snapshot
            JOIN dev_port port
              ON port.tenant_id = snapshot.tenant_id
             AND port.organization_id =
                 snapshot.organization_id
             AND port.deployment_id =
                 snapshot.deployment_id
             AND port.id = snapshot.port_id
            WHERE snapshot.id = ?
              AND snapshot.config_version_id = ?
              AND snapshot.port_id = ?
              AND snapshot.tenant_id = ?
              AND snapshot.organization_id = ?
              AND snapshot.deployment_id = ?
            """;

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final DeliveryCompletionFactsRefFactory factsRefFactory;
    private final ReliableEdgeConfirmationService confirmationService;
    private final ReliableDeviceTaskProofPort taskProofPort;
    private final TrustedDeviceTransportPresencePort transportPresence;

    public TrustedDeliveryCompletionService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            DeliveryCompletionFactsRefFactory factsRefFactory,
            ReliableEdgeConfirmationService confirmationService,
            ReliableDeviceTaskProofPort taskProofPort,
            TrustedDeviceTransportPresencePort transportPresence) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.factsRefFactory = factsRefFactory;
        this.confirmationService = confirmationService;
        this.taskProofPort = taskProofPort;
        this.transportPresence = transportPresence;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public TrustedDeviceEventApplyResult complete(
            TrustedDeviceInboxEvent event,
            DeliveryCompletionBusinessWriter businessWriter) {
        if (!MESSAGE_KIND.equals(event.messageKind())
                || event.normalizedSchemaVersion() != 1) {
            throw new IllegalArgumentException(
                    "unsupported delivery completion inbox message");
        }
        DeliveryCompletePhysicalFact fact =
                parse(event.normalizedPayload());
        requireNormalCompletion(fact);
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
            DeliveryCompletePhysicalFact fact,
            long inboxId,
            long tenantId,
            long organizationId,
            DeliveryCompletionBusinessWriter businessWriter) {
        AssetRow asset = lockAsset(fact);
        long deploymentId = lockDeployment(
                fact,
                asset.id(),
                tenantId,
                organizationId);
        transportPresence.observeAuthenticatedMessage(
                fact.hardwareSn(), inboxId);
        lockRuntime(deploymentId, tenantId, organizationId);
        SessionRow session = lockSession(
                fact,
                deploymentId,
                tenantId,
                organizationId);
        if ("BUSINESS_CONFIRMED".equals(session.status())) {
            return requirePreviouslyApplied(
                    fact,
                    inboxId,
                    tenantId,
                    organizationId,
                    deploymentId,
                    session);
        }
        lockPortRuntimeAndRequirePort(
                session.portId(),
                deploymentId,
                tenantId,
                organizationId);
        lockOccupancy(
                asset.id(),
                session.id(),
                deploymentId,
                tenantId,
                organizationId);
        CommandRow command = lockCommand(
                fact,
                session.id(),
                deploymentId,
                tenantId,
                organizationId);
        PortConfiguration portConfiguration =
                loadPortConfiguration(
                        session,
                        deploymentId,
                        tenantId,
                        organizationId);
        verifyFrozenFacts(fact, session, command, portConfiguration);

        List<ExistingEdge> collisions = findEdgeCollisions(
                fact,
                deploymentId,
                inboxId);
        if (!collisions.isEmpty()) {
            if (collisions.size() == 1
                    && collisions.getFirst().matches(
                            fact,
                            deploymentId,
                            inboxId)
                    && "BUSINESS_CONFIRMED".equals(session.status())) {
                return TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED;
            }
            throw new UntrustedInboxSourceException(
                    "delivery completion identity or sequence conflicts");
        }

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
                session,
                command,
                edgeEventId,
                tenantId,
                organizationId,
                deploymentId,
                receivedAt);

        DeliveryCompletionPersistenceFacts persistenceFacts =
                new DeliveryCompletionPersistenceFacts(
                        tenantId,
                        organizationId,
                        asset.id(),
                        deploymentId,
                        session.portId(),
                        session.id(),
                        session.organizationUserId(),
                        command.id(),
                        edgeEventId,
                        physicalResultId,
                        session.deviceConfigVersionId(),
                        session.portConfigSnapshotId(),
                        session.deliveryConfigVersionId(),
                        session.deliveryConfigContentSha256(),
                        session.bagId(),
                        session.bagUid(),
                        session.bagCode(),
                        session.unitPrice(),
                        session.openBalanceFloorCent(),
                        session.maxReviewAbsWeightGrams(),
                        session.negativeWeightThresholdGrams(),
                        portConfiguration.fullnessMode(),
                        portConfiguration.configuredFullWeightGrams(),
                        portConfiguration.fullnessSettleWaitMs(),
                        portConfiguration.fullnessConfirmationWaitMs(),
                        portConfiguration.fullnessMeasurementTimeoutMs(),
                        receivedAt,
                        fact);
        DeliveryCompletionBusinessResult business =
                businessWriter.write(
                        factsRefFactory.issue(persistenceFacts));

        mergeStartCommandSuccess(command, receivedAt);
        taskProofPort.completeFromTrustedProof(
                TASK_TYPE,
                TARGET_TYPE,
                fact.sessionUid().toString());
        requireSingle(jdbc.update("""
                        UPDATE dev_delivery_session
                        SET status = 'BUSINESS_CONFIRMED',
                            first_edge_accepted_at =
                                COALESCE(first_edge_accepted_at, ?),
                            first_physical_progress_at =
                                COALESCE(first_physical_progress_at, ?),
                            device_completed_at = ?,
                            ended_at = ?,
                            end_reason = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND status IN (
                              'AUTHORIZATION_QUEUED',
                              'IN_PROGRESS',
                              'RESULT_PENDING_RECOVERY'
                          )
                        """,
                receivedAt,
                receivedAt,
                receivedAt,
                receivedAt,
                fact.completionReason(),
                receivedAt,
                session.id(),
                tenantId,
                organizationId),
                "complete delivery session");
        requireSingle(jdbc.update("""
                        DELETE FROM dev_device_occupancy
                        WHERE asset_id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND occupancy_kind = 'DELIVERY'
                          AND delivery_session_id = ?
                        """,
                asset.id(),
                tenantId,
                organizationId,
                deploymentId,
                session.id()),
                "release delivery occupancy");
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
                "touch delivery runtime");
        confirmationService.registerApplied(
                tenantId,
                organizationId,
                deploymentId,
                fact.deploymentCode(),
                fact.eventUid().toString(),
                fact.payloadSha256(),
                "CREATED",
                business.resultReferences(),
                receivedAt);
        return TrustedDeviceEventApplyResult.APPLIED;
    }

    private TrustedDeviceEventApplyResult requirePreviouslyApplied(
            DeliveryCompletePhysicalFact fact,
            long inboxId,
            long tenantId,
            long organizationId,
            long deploymentId,
            SessionRow session) {
        lockPortRuntimeAndRequirePort(
                session.portId(),
                deploymentId,
                tenantId,
                organizationId);
        CommandRow command = lockCommand(
                fact,
                session.id(),
                deploymentId,
                tenantId,
                organizationId);
        PortConfiguration port = loadPortConfiguration(
                session,
                deploymentId,
                tenantId,
                organizationId);
        verifyFrozenFacts(fact, session, command, port);
        List<ExistingEdge> collisions = findEdgeCollisions(
                fact,
                deploymentId,
                inboxId);
        if (collisions.size() == 1
                && collisions.getFirst().matches(
                fact,
                deploymentId,
                inboxId)) {
            return TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED;
        }
        throw new UntrustedInboxSourceException(
                "completed delivery event identity conflicts");
    }

    private List<ExistingEdge> findEdgeCollisions(
            DeliveryCompletePhysicalFact fact,
            long deploymentId,
            long inboxId) {
        return jdbc.query(
                FIND_EDGE_COLLISIONS_SQL,
                (rs, ignored) -> new ExistingEdge(
                        rs.getLong("id"),
                        rs.getString("event_uid"),
                        rs.getLong("deployment_id"),
                        rs.getLong("edge_event_sequence"),
                        rs.getString("canonical_sha256"),
                        rs.getLong("source_inbox_id")),
                fact.eventUid().toString(),
                deploymentId,
                fact.edgeEventSequence(),
                inboxId);
    }

    private AssetRow lockAsset(
            DeliveryCompletePhysicalFact fact) {
        List<AssetRow> rows = jdbc.query("""
                        SELECT asset.id
                        FROM dev_device_asset asset
                        WHERE asset.hardware_sn = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new AssetRow(rs.getLong("id")),
                fact.hardwareSn());
        if (rows.size() != 1) {
            throw untrusted();
        }
        return rows.getFirst();
    }

    private long lockDeployment(
            DeliveryCompletePhysicalFact fact,
            long assetId,
            long tenantId,
            long organizationId) {
        List<Long> active = jdbc.query("""
                        SELECT deployment_id
                        FROM dev_asset_active_deployment
                        WHERE asset_id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("deployment_id"),
                assetId,
                tenantId,
                organizationId);
        if (active.size() != 1) {
            throw untrusted();
        }
        long deploymentId = active.getFirst();
        List<Long> rows = jdbc.query("""
                        SELECT id
                        FROM dev_device_deployment
                        WHERE id = ?
                          AND asset_id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND public_code = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("id"),
                deploymentId,
                assetId,
                tenantId,
                organizationId,
                fact.deploymentCode());
        if (rows.size() != 1) {
            throw untrusted();
        }
        return deploymentId;
    }

    private void lockRuntime(
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
            throw untrusted();
        }
    }

    private SessionRow lockSession(
            DeliveryCompletePhysicalFact fact,
            long deploymentId,
            long tenantId,
            long organizationId) {
        List<SessionRow> rows = jdbc.query("""
                        SELECT id, port_id, organization_user_id,
                               device_config_version_id,
                               device_config_version_no,
                               device_config_content_sha256,
                               device_config_mcu_payload_sha256,
                               port_config_snapshot_id,
                               delivery_config_version_id,
                               delivery_config_content_sha256,
                               bag_id, bag_uid_snapshot,
                               bag_code_snapshot, status,
                               unit_price_yuan_per_kg,
                               open_balance_floor_cent,
                               max_review_abs_weight_g,
                               negative_weight_anomaly_threshold_g
                        FROM dev_delivery_session
                        WHERE session_uid = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> session(rs),
                fact.sessionUid().toString(),
                tenantId,
                organizationId,
                deploymentId);
        if (rows.size() != 1
                || !Set.of(
                        "AUTHORIZATION_QUEUED",
                        "IN_PROGRESS",
                        "RESULT_PENDING_RECOVERY",
                        "BUSINESS_CONFIRMED")
                .contains(rows.getFirst().status())) {
            throw untrusted();
        }
        return rows.getFirst();
    }

    private void lockOccupancy(
            long assetId,
            long sessionId,
            long deploymentId,
            long tenantId,
            long organizationId) {
        List<Long> rows = jdbc.query("""
                        SELECT delivery_session_id
                        FROM dev_device_occupancy
                        WHERE asset_id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND occupancy_kind = 'DELIVERY'
                        FOR UPDATE
                        """,
                (rs, ignored) ->
                        rs.getLong("delivery_session_id"),
                assetId,
                tenantId,
                organizationId,
                deploymentId);
        if (rows.size() != 1 || rows.getFirst() != sessionId) {
            throw untrusted();
        }
    }

    private CommandRow lockCommand(
            DeliveryCompletePhysicalFact fact,
            long sessionId,
            long deploymentId,
            long tenantId,
            long organizationId) {
        List<CommandRow> rows = jdbc.query("""
                        SELECT id, command_uid, physical_state
                        FROM dev_device_command
                        WHERE command_uid = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND command_type =
                              'START_DELIVERY_SESSION'
                          AND delivery_session_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new CommandRow(
                        rs.getLong("id"),
                        UUID.fromString(rs.getString("command_uid")),
                        rs.getString("physical_state")),
                fact.commandUid().toString(),
                tenantId,
                organizationId,
                deploymentId,
                sessionId);
        if (rows.size() != 1) {
            throw untrusted();
        }
        return rows.getFirst();
    }

    private void mergeStartCommandSuccess(
            CommandRow command,
            LocalDateTime completedAt) {
        if ("PHYSICAL_SUCCEEDED".equals(command.physicalState())) {
            return;
        }
        if (!Set.of(
                "QUEUED",
                "EDGE_ACCEPTED",
                "PHYSICAL_STARTED")
                .contains(command.physicalState())) {
            throw new UntrustedInboxSourceException(
                    "delivery completion conflicts with command state");
        }
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
                          AND physical_state = ?
                        """,
                completedAt,
                completedAt,
                completedAt,
                completedAt,
                command.id(),
                command.physicalState()),
                "merge delivery command completion");
    }

    private void lockPortRuntimeAndRequirePort(
            long portId,
            long deploymentId,
            long tenantId,
            long organizationId) {
        List<Long> runtime = jdbc.query(
                LOCK_PORT_RUNTIME_SQL,
                (rs, ignored) -> rs.getLong("port_id"),
                portId,
                tenantId,
                organizationId,
                deploymentId);
        List<Long> ports = jdbc.query(
                LOAD_PORT_SQL,
                (rs, ignored) -> rs.getLong("id"),
                portId,
                tenantId,
                organizationId,
                deploymentId);
        if (ports.size() != 1 || runtime.size() != 1) {
            throw untrusted();
        }
    }

    private PortConfiguration loadPortConfiguration(
            SessionRow session,
            long deploymentId,
            long tenantId,
            long organizationId) {
        List<PortConfiguration> rows = jdbc.query(
                LOAD_PORT_CONFIGURATION_SQL,
                (rs, ignored) -> new PortConfiguration(
                        rs.getInt("port_no"),
                        rs.getString("fullness_mode"),
                        rs.getLong("configured_full_weight_g"),
                        rs.getLong("fullness_settle_wait_ms"),
                        rs.getLong("fullness_confirmation_wait_ms"),
                        rs.getLong("weight_measurement_timeout_ms"),
                        rs.getInt("weight_required_sample_count"),
                        rs.getLong("weight_minimum_g"),
                        rs.getLong("weight_maximum_g"),
                        rs.getLong("calibration_version")),
                session.portConfigSnapshotId(),
                session.deviceConfigVersionId(),
                session.portId(),
                tenantId,
                organizationId,
                deploymentId);
        if (rows.size() != 1) {
            throw untrusted();
        }
        return rows.getFirst();
    }

    private static void verifyFrozenFacts(
            DeliveryCompletePhysicalFact fact,
            SessionRow session,
            CommandRow command,
            PortConfiguration port) {
        long expectedPrice = session.unitPrice()
                .movePointRight(4)
                .longValueExact();
        if (!fact.commandUid().equals(command.uid())
                || fact.portNo() != port.portNo()
                || fact.configurationVersion()
                != session.deviceConfigVersionNo()
                || !fact.configurationContentSha256().equals(
                        HexFormat.of().formatHex(
                                session.deviceConfigContentSha256()))
                || !fact.configurationMcuPayloadSha256().equals(
                        HexFormat.of().formatHex(
                                session.deviceConfigMcuPayloadSha256()))
                || fact.unitPriceTenThousandths() != expectedPrice) {
            throw untrusted();
        }
        requireMeasurementMatchesFrozenPort(
                fact.firstPreOpenMeasurement(),
                port);
        requireMeasurementMatchesFrozenPort(
                fact.finalPostCloseMeasurement(),
                port);
    }

    private static void requireMeasurementMatchesFrozenPort(
            DeliveryCompleteMeasurement measurement,
            PortConfiguration port) {
        if (!measurementMatchesFrozenPort(
                measurement,
                port.calibrationVersion(),
                port.weightMinimumGrams(),
                port.weightMaximumGrams())) {
            throw untrusted();
        }
    }

    static boolean measurementMatchesFrozenPort(
            DeliveryCompleteMeasurement measurement,
            long calibrationVersion,
            long minimumWeightGrams,
            long maximumWeightGrams) {
        long weight = measurement.reportedWeightGrams();
        return measurement.calibrationVersion() == calibrationVersion
                && measurement.sampleCount() >= 1
                && weight >= minimumWeightGrams
                && weight <= maximumWeightGrams;
    }

    private long insertEdgeEvent(
            DeliveryCompletePhysicalFact fact,
            long inboxId,
            long tenantId,
            long organizationId,
            long deploymentId,
            LocalDateTime receivedAt) {
        requireSingle(jdbc.update("""
                        INSERT INTO dev_edge_event (
                            event_uid, tenant_id, organization_id,
                            deployment_id, edge_event_sequence,
                            event_type, delivery_class, schema_version,
                            target_type, target_stable_key_sha256,
                            device_occurred_at, clock_quality,
                            backend_received_at, payload_sha256,
                            canonical_sha256, source_inbox_id, created_at
                        ) VALUES (
                            ?, ?, ?, ?, ?,
                            'DELIVERY_COMPLETE', 'RELIABLE_FACT', 1,
                            'DELIVERY_SESSION', ?,
                            ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                fact.eventUid().toString(),
                tenantId,
                organizationId,
                deploymentId,
                fact.edgeEventSequence(),
                sha256(fact.sessionUid().toString()),
                instant(fact.deviceOccurredAt()),
                fact.clockQuality(),
                receivedAt,
                digest(fact.payloadSha256()),
                digest(fact.canonicalSha256()),
                inboxId,
                receivedAt),
                "insert delivery edge event");
        Long id = jdbc.queryForObject("""
                        SELECT id
                        FROM dev_edge_event
                        WHERE event_uid = ?
                        """,
                Long.class,
                fact.eventUid().toString());
        if (id == null) {
            throw new IllegalStateException(
                    "delivery edge event id is missing");
        }
        return id;
    }

    private long insertPhysicalResult(
            DeliveryCompletePhysicalFact fact,
            SessionRow session,
            CommandRow command,
            long edgeEventId,
            long tenantId,
            long organizationId,
            long deploymentId,
            LocalDateTime receivedAt) {
        DeliveryCompleteMeasurement before =
                fact.firstPreOpenMeasurement();
        DeliveryCompleteMeasurement after =
                fact.finalPostCloseMeasurement();
        DeliveryCompleteDoorCommand door =
                fact.finalDoorCommand();
        requireSingle(jdbc.update("""
                        INSERT INTO dev_physical_result (
                            tenant_id, organization_id, deployment_id,
                            port_id, edge_event_id, edge_event_type,
                            command_id, command_type,
                            reported_config_version_no,
                            reported_config_content_sha256,
                            reported_config_mcu_payload_sha256,
                            result_type, delivery_session_id,
                            delivery_pre_measurement_uid,
                            delivery_pre_measurement_status,
                            delivery_pre_weight_g,
                            delivery_pre_last_observed_weight_g,
                            delivery_pre_weight_value_available,
                            delivery_pre_weight_value_kind,
                            delivery_pre_measurement_elapsed_ms,
                            delivery_pre_sample_count,
                            delivery_pre_calibration_version,
                            delivery_pre_sensor_health,
                            delivery_pre_fault_code,
                            delivery_pre_mcu_boot_id,
                            delivery_pre_mcu_event_sequence,
                            delivery_post_measurement_uid,
                            delivery_post_measurement_status,
                            delivery_post_weight_g,
                            delivery_post_last_observed_weight_g,
                            delivery_post_weight_value_available,
                            delivery_post_weight_value_kind,
                            delivery_post_measurement_elapsed_ms,
                            delivery_post_sample_count,
                            delivery_post_calibration_version,
                            delivery_post_sensor_health,
                            delivery_post_fault_code,
                            delivery_post_mcu_boot_id,
                            delivery_post_mcu_event_sequence,
                            delivery_net_weight_g,
                            delivery_final_door_command,
                            delivery_final_door_output_status,
                            delivery_final_door_physical_state_basis,
                            delivery_completion_reason,
                            delivery_manual_review_required,
                            negative_weight_anomaly,
                            created_at
                        ) VALUES (
                            ?, ?, ?, ?,
                            ?, 'DELIVERY_COMPLETE',
                            ?, 'START_DELIVERY_SESSION',
                            ?, ?, ?,
                            'DELIVERY', ?,
                            ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                tenantId,
                organizationId,
                deploymentId,
                session.portId(),
                edgeEventId,
                command.id(),
                session.deviceConfigVersionNo(),
                session.deviceConfigContentSha256(),
                session.deviceConfigMcuPayloadSha256(),
                session.id(),
                before.measurementUid().toString(),
                before.status(),
                before.reportedWeightGrams(),
                before.weightValueAvailable(),
                before.weightValueKind(),
                before.measurementElapsedMs(),
                before.sampleCount(),
                before.calibrationVersion(),
                before.sensorHealth(),
                before.faultCode(),
                before.mcuBootId(),
                before.mcuEventSequence(),
                after.measurementUid().toString(),
                after.status(),
                after.reportedWeightGrams(),
                after.weightValueAvailable(),
                after.weightValueKind(),
                after.measurementElapsedMs(),
                after.sampleCount(),
                after.calibrationVersion(),
                after.sensorHealth(),
                after.faultCode(),
                after.mcuBootId(),
                after.mcuEventSequence(),
                fact.deliveryNetWeightGrams(),
                door.command(),
                door.outputStatus(),
                door.physicalStateBasis(),
                fact.completionReason(),
                fact.manualReviewRequired(),
                fact.negativeWeightAnomaly(),
                receivedAt),
                "insert delivery physical result");
        Long id = jdbc.queryForObject("""
                        SELECT id
                        FROM dev_physical_result
                        WHERE delivery_session_id = ?
                        """,
                Long.class,
                session.id());
        if (id == null) {
            throw new IllegalStateException(
                    "delivery physical result id is missing");
        }
        return id;
    }

    private DeliveryCompletePhysicalFact parse(String normalizedPayload) {
        JsonNode root = objectMapper.readTree(normalizedPayload);
        JsonNode source = requiredObject(root, "trustedSource");
        JsonNode event = requiredObject(root, "event");
        JsonNode target = requiredObject(event, "target");
        JsonNode payload = requiredObject(event, "payload");
        if (!MESSAGE_KIND.equals(requiredText(event, "eventType"))
                || !"RELIABLE_FACT".equals(
                requiredText(event, "deliveryClass"))
                || !"DELIVERY_SESSION".equals(
                requiredText(target, "type"))) {
            throw new IllegalArgumentException(
                    "delivery completion envelope constants differ");
        }
        UUID sessionUid = uuid(payload, "sessionUid");
        if (!sessionUid.toString().equals(
                requiredText(target, "uid"))) {
            throw new IllegalArgumentException(
                    "delivery completion target differs from session");
        }
        String sourceHardwareSn = requiredText(source, "deviceName");
        JsonNode frozen = requiredObject(payload, "frozenConfig");
        List<DeliveryCompletePhoto> photos = new ArrayList<>();
        JsonNode photoArray = requiredArray(payload, "photos");
        photoArray.forEach(node -> photos.add(photo(node)));
        return new DeliveryCompletePhysicalFact(
                uuid(event, "eventUid"),
                uuid(event, "commandUid"),
                sessionUid,
                sourceHardwareSn,
                requiredText(event, "deploymentCode"),
                positiveLong(event, "edgeEventSequence"),
                nullableInstant(event, "occurredAt"),
                requiredText(event, "clockQuality"),
                requiredText(event, "payloadSha256"),
                requiredText(root, "eventCanonicalSha256"),
                Math.toIntExact(positiveLong(payload, "portNo")),
                measurement(payload.get("firstPreOpenMeasurement")),
                measurement(payload.get("finalPostCloseMeasurement")),
                nullableLong(payload, "deliveryNetWeightGrams"),
                door(payload.get("finalDoorCommand")),
                requiredText(payload, "completionReason"),
                requiredBoolean(payload, "manualReviewRequired"),
                requiredBoolean(payload, "negativeWeightAnomaly"),
                positiveLong(frozen, "version"),
                requiredText(frozen, "contentSha256"),
                requiredText(frozen, "mcuPayloadSha256"),
                positiveLong(payload, "unitPriceTenThousandths"),
                photos);
    }

    private static DeliveryCompleteMeasurement measurement(JsonNode node) {
        if (node == null || node.isNull()) {
            return null;
        }
        return new DeliveryCompleteMeasurement(
                uuid(node, "measurementUid"),
                requiredText(node, "status"),
                requiredBoolean(node, "weightValueAvailable"),
                nullableLong(node, "reportedWeightGrams"),
                requiredText(node, "weightValueKind"),
                nonNegativeLong(node, "measurementElapsedMs"),
                Math.toIntExact(nonNegativeLong(node, "sampleCount")),
                nonNegativeLong(node, "calibrationVersion"),
                requiredText(node, "sensorHealth"),
                nullableText(node, "faultCode"),
                positiveLong(node, "mcuBootId"),
                positiveLong(node, "mcuEventSequence"));
    }

    private static DeliveryCompleteDoorCommand door(JsonNode node) {
        if (node == null || node.isNull()) {
            return null;
        }
        return new DeliveryCompleteDoorCommand(
                requiredText(node, "command"),
                requiredText(node, "outputStatus"),
                requiredText(node, "physicalStateBasis"));
    }

    private static DeliveryCompletePhoto photo(JsonNode node) {
        return new DeliveryCompletePhoto(
                requiredText(node, "slot"),
                requiredText(node, "status"),
                nullableUuid(node, "photoUid"),
                nullableText(node, "url"),
                nullableText(node, "sha256"),
                nullableLong(node, "sizeBytes"),
                nullableInstant(node, "capturedAt"),
                nullableText(node, "missingReason"));
    }

    private static void requireNormalCompletion(
            DeliveryCompletePhysicalFact fact) {
        DeliveryCompleteMeasurement before =
                fact.firstPreOpenMeasurement();
        DeliveryCompleteMeasurement after =
                fact.finalPostCloseMeasurement();
        DeliveryCompleteDoorCommand door =
                fact.finalDoorCommand();
        if (before == null
                || after == null
                || !"STABLE".equals(before.status())
                || !"STABLE".equals(after.status())
                || !before.weightValueAvailable()
                || !after.weightValueAvailable()
                || !"STABLE_WINDOW_MEAN".equals(
                        before.weightValueKind())
                || !"STABLE_WINDOW_MEAN".equals(
                        after.weightValueKind())
                || before.sampleCount() < 1
                || after.sampleCount() < 1
                || !"OK".equals(before.sensorHealth())
                || !"OK".equals(after.sensorHealth())
                || before.faultCode() != null
                || after.faultCode() != null
                || before.reportedWeightGrams() == null
                || after.reportedWeightGrams() == null
                || before.measurementUid().equals(
                        after.measurementUid())
                || (before.mcuBootId() == after.mcuBootId()
                && before.mcuEventSequence()
                == after.mcuEventSequence())
                || fact.deliveryNetWeightGrams() == null
                || door == null
                || !"CLOSE".equals(door.command())
                || !Set.of(
                        "COMMAND_DISPATCHED",
                        "COALESCED_WITH_EXISTING_CLOSE")
                .contains(door.outputStatus())
                || !"NOT_OBSERVABLE".equals(
                        door.physicalStateBasis())
                || fact.manualReviewRequired()
                || !Set.of(
                        "USER_ENDED",
                        "SELECTION_WINDOW_EXPIRED")
                .contains(fact.completionReason())
                || fact.photos().size() != 4
                || !fact.photos().stream()
                .map(DeliveryCompletePhoto::slot)
                .collect(java.util.stream.Collectors.toSet())
                .equals(Set.of(
                        "BEFORE_INNER",
                        "BEFORE_OUTER",
                        "AFTER_INNER",
                        "AFTER_OUTER"))) {
            throw new IllegalArgumentException(
                    "only the normal stable delivery completion is supported");
        }
    }

    private LocalDateTime databaseNow() {
        return jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)",
                LocalDateTime.class);
    }

    private static SessionRow session(ResultSet rs)
            throws SQLException {
        return new SessionRow(
                rs.getLong("id"),
                rs.getLong("port_id"),
                rs.getLong("organization_user_id"),
                rs.getLong("device_config_version_id"),
                rs.getLong("device_config_version_no"),
                rs.getBytes("device_config_content_sha256"),
                rs.getBytes("device_config_mcu_payload_sha256"),
                rs.getLong("port_config_snapshot_id"),
                rs.getLong("delivery_config_version_id"),
                rs.getBytes("delivery_config_content_sha256"),
                rs.getLong("bag_id"),
                UUID.fromString(rs.getString("bag_uid_snapshot")),
                rs.getString("bag_code_snapshot"),
                rs.getString("status"),
                rs.getBigDecimal("unit_price_yuan_per_kg"),
                rs.getLong("open_balance_floor_cent"),
                rs.getLong("max_review_abs_weight_g"),
                rs.getLong(
                        "negative_weight_anomaly_threshold_g"));
    }

    private static JsonNode requiredObject(
            JsonNode parent,
            String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isObject()) {
            throw new IllegalArgumentException(
                    field + " must be an object");
        }
        return value;
    }

    private static JsonNode requiredArray(
            JsonNode parent,
            String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isArray()) {
            throw new IllegalArgumentException(
                    field + " must be an array");
        }
        return value;
    }

    private static String requiredText(
            JsonNode parent,
            String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isTextual()
                || value.asText().isBlank()) {
            throw new IllegalArgumentException(
                    field + " must be text");
        }
        return value.asText();
    }

    private static String nullableText(
            JsonNode parent,
            String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || value.isNull()) {
            return null;
        }
        if (!value.isTextual()) {
            throw new IllegalArgumentException(
                    field + " must be nullable text");
        }
        return value.asText();
    }

    private static boolean requiredBoolean(
            JsonNode parent,
            String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isBoolean()) {
            throw new IllegalArgumentException(
                    field + " must be a boolean");
        }
        return value.booleanValue();
    }

    private static UUID uuid(JsonNode parent, String field) {
        return UUID.fromString(requiredText(parent, field));
    }

    private static UUID nullableUuid(JsonNode parent, String field) {
        String value = nullableText(parent, field);
        return value == null ? null : UUID.fromString(value);
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

    private static Long nullableLong(
            JsonNode parent,
            String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || value.isNull()) {
            return null;
        }
        try {
            return value.decimalValue().longValueExact();
        } catch (RuntimeException exception) {
            throw new IllegalArgumentException(
                    field + " must be an integer",
                    exception);
        }
    }

    private static long exactLong(
            JsonNode parent,
            String field) {
        Long value = nullableLong(parent, field);
        if (value == null) {
            throw new IllegalArgumentException(
                    field + " must be an integer");
        }
        return value;
    }

    private static Instant nullableInstant(
            JsonNode parent,
            String field) {
        String value = nullableText(parent, field);
        return value == null ? null : Instant.parse(value);
    }

    private static LocalDateTime instant(Instant value) {
        return value == null
                ? null
                : LocalDateTime.ofInstant(value, ZoneOffset.UTC);
    }

    private static byte[] digest(String value) {
        return HexFormat.of().parseHex(value);
    }

    private static byte[] sha256(String value) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(
                    value.getBytes(StandardCharsets.UTF_8));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException(
                    "SHA-256 is unavailable",
                    exception);
        }
    }

    private static void requireSingle(
            int updated,
            String operation) {
        if (updated != 1) {
            throw new IllegalStateException(
                    operation + " affected " + updated + " rows");
        }
    }

    private static UntrustedInboxSourceException untrusted() {
        return new UntrustedInboxSourceException(
                "delivery completion target is not authoritative");
    }

    private record AssetRow(long id) {
    }

    private record CommandRow(
            long id,
            UUID uid,
            String physicalState) {
    }

    private record PortConfiguration(
            int portNo,
            String fullnessMode,
            long configuredFullWeightGrams,
            long fullnessSettleWaitMs,
            long fullnessConfirmationWaitMs,
            long fullnessMeasurementTimeoutMs,
            int weightRequiredSampleCount,
            long weightMinimumGrams,
            long weightMaximumGrams,
            long calibrationVersion) {
    }

    private record SessionRow(
            long id,
            long portId,
            long organizationUserId,
            long deviceConfigVersionId,
            long deviceConfigVersionNo,
            byte[] deviceConfigContentSha256,
            byte[] deviceConfigMcuPayloadSha256,
            long portConfigSnapshotId,
            long deliveryConfigVersionId,
            byte[] deliveryConfigContentSha256,
            long bagId,
            UUID bagUid,
            String bagCode,
            String status,
            BigDecimal unitPrice,
            long openBalanceFloorCent,
            long maxReviewAbsWeightGrams,
            long negativeWeightThresholdGrams) {
    }

    private record ExistingEdge(
            long id,
            String eventUid,
            long deploymentId,
            long sequence,
            String canonicalSha256,
            long inboxId) {

        private boolean matches(
                DeliveryCompletePhysicalFact fact,
                long expectedDeploymentId,
                long expectedInboxId) {
            return eventUid.equals(fact.eventUid().toString())
                    && deploymentId == expectedDeploymentId
                    && sequence == fact.edgeEventSequence()
                    && canonicalSha256.equals(
                    fact.canonicalSha256())
                    && inboxId == expectedInboxId;
        }
    }
}
