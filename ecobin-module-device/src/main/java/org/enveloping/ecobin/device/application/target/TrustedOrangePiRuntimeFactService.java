package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.port.ApplyTrustedPhotoStatusBusinessPort;
import org.enveloping.ecobin.device.api.result.PhotoStatusBusinessResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;
import org.enveloping.ecobin.device.api.result.TrustedPhotoStatusFact;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskProofPort;
import org.enveloping.ecobin.framework.reliability.TrustedInboxQuarantinePort;
import org.enveloping.ecobin.framework.reliability.TrustedOrganizationInboxRefFactory;
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
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/**
 * Applies trusted runtime facts reported by the Orange Pi through OneNet.
 *
 * <p>MCU and UART fields are retained as diagnostics. They are deliberately
 * not interpreted as backend activation gates.</p>
 */
@Service
public class TrustedOrangePiRuntimeFactService {

    private static final Set<String> SUPPORTED = Set.of(
            "DEVICE_COMMAND_OBSERVED",
            "DEVICE_RUNTIME_SNAPSHOT",
            "DEVICE_FAULT_OBSERVED",
            "DEVICE_FAULT_RECOVERED",
            "SAFETY_SENSOR_STATE_CHANGED",
            "PHOTO_STATUS_REPORTED",
            "PHOTO_UPLOAD_GRANT_REQUESTED",
            "BUSINESS_CONFIRMATION_RECEIPT");

    static final String LOAD_COMMAND_STAGE_SQL = """
            SELECT
                event_row.edge_event_id,
                event_row.mcu_command_uid,
                event_row.error_code
            FROM dev_device_command_event event_row
            WHERE event_row.command_id = ?
              AND event_row.observation_stage = ?
            """;

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final ReliableEdgeConfirmationService confirmationService;
    private final ReliableDeviceTaskProofPort taskProofPort;
    private final TrustedInboxQuarantinePort quarantinePort;
    private final TrustedOrganizationInboxRefFactory inboxRefFactory;
    private final ApplyTrustedPhotoStatusBusinessPort photoStatusBusiness;
    private final ReliablePhotoUploadGrantService photoUploadGrants;

    public TrustedOrangePiRuntimeFactService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            ReliableEdgeConfirmationService confirmationService,
            ReliableDeviceTaskProofPort taskProofPort,
            TrustedInboxQuarantinePort quarantinePort,
            TrustedOrganizationInboxRefFactory inboxRefFactory,
            ApplyTrustedPhotoStatusBusinessPort photoStatusBusiness,
            ReliablePhotoUploadGrantService photoUploadGrants) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.confirmationService = confirmationService;
        this.taskProofPort = taskProofPort;
        this.quarantinePort = quarantinePort;
        this.inboxRefFactory = inboxRefFactory;
        this.photoStatusBusiness = photoStatusBusiness;
        this.photoUploadGrants = photoUploadGrants;
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public TrustedDeviceEventApplyResult apply(
            TrustedDeviceInboxEvent inboxEvent) {
        if (!SUPPORTED.contains(inboxEvent.messageKind())
                || inboxEvent.normalizedSchemaVersion() != 1) {
            throw new IllegalArgumentException(
                    "unsupported trusted Orange Pi inbox message");
        }
        ParsedEvent event = parse(
                inboxEvent.messageKind(),
                inboxEvent.normalizedPayload());
        return inboxEvent.sourceInbox().use(
                (inboxId, tenantId, organizationId) ->
                        applyWithinScope(
                                event,
                                inboxId,
                                tenantId,
                                organizationId));
    }

    private TrustedDeviceEventApplyResult applyWithinScope(
            ParsedEvent event,
            long inboxId,
            long tenantId,
            long organizationId) {
        DeploymentTarget deployment = loadDeployment(
                event, tenantId, organizationId);
        EdgeInsert edge = insertEdgeEvent(
                event,
                deployment,
                inboxId,
                tenantId,
                organizationId);
        if (!edge.inserted()) {
            return edge.quarantined()
                    ? TrustedDeviceEventApplyResult.QUARANTINED
                    : TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED;
        }

        String effectKind;
        switch (event.eventType()) {
            case "DEVICE_COMMAND_OBSERVED" -> {
                CommandObservationResult result =
                        applyCommandObservation(
                                event,
                                deployment,
                                edge.eventId(),
                                tenantId,
                                organizationId,
                                edge.receivedAt());
                if (result.conflict()) {
                    UUID quarantineUid =
                            quarantinePort.quarantineIdentityConflict(
                                    inboxRefFactory.issue(
                                            inboxId,
                                            tenantId,
                                            organizationId),
                                    "device command stage was reported "
                                            + "with another event identity");
                    confirmationService.registerQuarantined(
                            tenantId,
                            organizationId,
                            deployment.deploymentId(),
                            event.deploymentCode(),
                            event.eventUid(),
                            event.payloadSha256(),
                            "DEVICE_COMMAND_STAGE_CONFLICT",
                            quarantineUid,
                            edge.receivedAt());
                    return TrustedDeviceEventApplyResult.QUARANTINED;
                }
                effectKind = result.effectKind();
            }
            case "DEVICE_RUNTIME_SNAPSHOT" -> {
                applyRuntimeSnapshot(
                        event,
                        deployment,
                        edge.eventId(),
                        tenantId,
                        organizationId,
                        edge.receivedAt());
                return TrustedDeviceEventApplyResult.APPLIED;
            }
            case "DEVICE_FAULT_OBSERVED" -> {
                FaultApplyResult result = observeFault(
                        event,
                        deployment,
                        edge.eventId(),
                        tenantId,
                        organizationId,
                        edge.receivedAt());
                if (result.conflict()) {
                    return quarantineFaultConflict(
                            event,
                            deployment,
                            inboxId,
                            tenantId,
                            organizationId,
                            edge.receivedAt(),
                            result);
                }
                effectKind = result.effectKind();
            }
            case "DEVICE_FAULT_RECOVERED" -> {
                FaultApplyResult result = observeFaultRecovery(
                        event,
                        deployment,
                        edge.eventId(),
                        tenantId,
                        organizationId,
                        edge.receivedAt());
                if (result.conflict()) {
                    return quarantineFaultConflict(
                            event,
                            deployment,
                            inboxId,
                            tenantId,
                            organizationId,
                            edge.receivedAt(),
                            result);
                }
                effectKind = result.effectKind();
            }
            case "SAFETY_SENSOR_STATE_CHANGED" -> {
                applySafetyChange(
                        event,
                        deployment,
                        edge.eventId(),
                        tenantId,
                        organizationId,
                        edge.receivedAt());
                effectKind = "UPDATED";
            }
            case "PHOTO_STATUS_REPORTED" -> {
                PhotoStatusBusinessResult result =
                        photoStatusBusiness.apply(photoStatusFact(
                                event,
                                deployment,
                                edge.eventId(),
                                tenantId,
                                organizationId,
                                edge.receivedAt()));
                if (result.outcome()
                        == PhotoStatusBusinessResult.Outcome.CONFLICT) {
                    UUID quarantineUid =
                            quarantinePort.quarantineIdentityConflict(
                                    inboxRefFactory.issue(
                                            inboxId,
                                            tenantId,
                                            organizationId),
                                    "photo terminal fact conflicts with "
                                            + "the accepted work slot");
                    confirmationService.registerQuarantined(
                            tenantId,
                            organizationId,
                            deployment.deploymentId(),
                            event.deploymentCode(),
                            event.eventUid(),
                            event.payloadSha256(),
                            result.conflictCode(),
                            quarantineUid,
                            edge.receivedAt());
                    return TrustedDeviceEventApplyResult.QUARANTINED;
                }
                confirmationService.registerApplied(
                        tenantId,
                        organizationId,
                        deployment.deploymentId(),
                        event.deploymentCode(),
                        event.eventUid(),
                        event.payloadSha256(),
                        result.effectKind(),
                        result.resultReferences(),
                        edge.receivedAt());
                return TrustedDeviceEventApplyResult.APPLIED;
            }
            case "PHOTO_UPLOAD_GRANT_REQUESTED" -> {
                ReliablePhotoUploadGrantService
                        .PhotoGrantRequestApplyResult result =
                        photoUploadGrants.apply(
                                tenantId,
                                organizationId,
                                deployment.deploymentId(),
                                edge.eventId(),
                                event.deploymentCode(),
                                event.eventUid(),
                                event.payload(),
                                event.occurredAt(),
                                edge.receivedAt());
                if (result.conflict()) {
                    UUID quarantineUid =
                            quarantinePort.quarantineIdentityConflict(
                                    inboxRefFactory.issue(
                                            inboxId,
                                            tenantId,
                                            organizationId),
                                    "photo grant request targets an "
                                            + "unknown work identity");
                    confirmationService.registerQuarantined(
                            tenantId,
                            organizationId,
                            deployment.deploymentId(),
                            event.deploymentCode(),
                            event.eventUid(),
                            event.payloadSha256(),
                            result.conflictCode(),
                            quarantineUid,
                            edge.receivedAt());
                    return TrustedDeviceEventApplyResult.QUARANTINED;
                }
                confirmationService.registerApplied(
                        tenantId,
                        organizationId,
                        deployment.deploymentId(),
                        event.deploymentCode(),
                        event.eventUid(),
                        event.payloadSha256(),
                        result.effectKind(),
                        edge.receivedAt());
                return TrustedDeviceEventApplyResult.APPLIED;
            }
            case "BUSINESS_CONFIRMATION_RECEIPT" -> {
                applyConfirmationReceipt(
                        event,
                        deployment);
                return TrustedDeviceEventApplyResult.APPLIED;
            }
            default -> throw new IllegalStateException(
                    "trusted event router lost its event type");
        }

        confirmationService.registerApplied(
                tenantId,
                organizationId,
                deployment.deploymentId(),
                event.deploymentCode(),
                event.eventUid(),
                event.payloadSha256(),
                effectKind,
                edge.receivedAt());
        return TrustedDeviceEventApplyResult.APPLIED;
    }

    private TrustedDeviceEventApplyResult quarantineFaultConflict(
            ParsedEvent event,
            DeploymentTarget deployment,
            long inboxId,
            long tenantId,
            long organizationId,
            LocalDateTime receivedAt,
            FaultApplyResult result) {
        UUID quarantineUid =
                quarantinePort.quarantineIdentityConflict(
                        inboxRefFactory.issue(
                                inboxId,
                                tenantId,
                                organizationId),
                        result.detail());
        confirmationService.registerQuarantined(
                tenantId,
                organizationId,
                deployment.deploymentId(),
                event.deploymentCode(),
                event.eventUid(),
                event.payloadSha256(),
                result.conflictCode(),
                quarantineUid,
                receivedAt);
        return TrustedDeviceEventApplyResult.QUARANTINED;
    }

    private DeploymentTarget loadDeployment(
            ParsedEvent event,
            long tenantId,
            long organizationId) {
        List<LockedAsset> assets = jdbc.query("""
                        SELECT id, expected_port_count
                        FROM dev_device_asset
                        WHERE hardware_sn = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new LockedAsset(
                        rs.getLong("id"),
                        rs.getInt("expected_port_count")),
                event.hardwareSn());
        if (assets.size() != 1) {
            throw new UntrustedInboxSourceException(
                    "authenticated Orange Pi asset is not authoritative");
        }
        LockedAsset asset = assets.getFirst();
        List<Long> deploymentIds = jdbc.query("""
                        SELECT id
                        FROM dev_device_deployment
                        WHERE asset_id = ?
                          AND public_code = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("id"),
                asset.assetId(),
                event.deploymentCode(),
                tenantId,
                organizationId);
        if (deploymentIds.size() != 1) {
            throw new UntrustedInboxSourceException(
                    "authenticated Orange Pi deployment is not authoritative");
        }
        return new DeploymentTarget(
                asset.assetId(),
                deploymentIds.getFirst(),
                asset.portCount());
    }

    private EdgeInsert insertEdgeEvent(
            ParsedEvent event,
            DeploymentTarget deployment,
            long inboxId,
            long tenantId,
            long organizationId) {
        List<ExistingEdge> collisions = jdbc.query("""
                        SELECT
                            id, event_uid, edge_event_sequence,
                            LOWER(HEX(canonical_sha256)) canonical_sha256,
                            source_inbox_id
                        FROM dev_edge_event
                        WHERE event_uid = ?
                           OR (
                               deployment_id = ?
                               AND edge_event_sequence = ?
                           )
                        """,
                (rs, ignored) -> new ExistingEdge(
                        rs.getLong("id"),
                        rs.getString("event_uid"),
                        rs.getLong("edge_event_sequence"),
                        rs.getString("canonical_sha256"),
                        rs.getLong("source_inbox_id")),
                event.eventUid(),
                deployment.deploymentId(),
                event.sequence());
        if (!collisions.isEmpty()) {
            if (collisions.size() == 1
                    && collisions.getFirst().matches(event, inboxId)) {
                return new EdgeInsert(
                        collisions.getFirst().id(),
                        false,
                        false,
                        null);
            }
            var quarantineUid =
                    quarantinePort.quarantineIdentityConflict(
                    inboxRefFactory.issue(
                            inboxId,
                            tenantId,
                            organizationId),
                    "trusted Orange Pi event identity or sequence conflicts");
            if ("RELIABLE_FACT".equals(event.deliveryClass())) {
                confirmationService.registerQuarantined(
                        tenantId,
                        organizationId,
                        deployment.deploymentId(),
                        event.deploymentCode(),
                        event.eventUid(),
                        event.payloadSha256(),
                        "EVENT_IDENTITY_CONFLICT",
                        quarantineUid,
                        databaseNow());
            }
            return new EdgeInsert(0, false, true, null);
        }

        LocalDateTime now = databaseNow();
        int inserted = jdbc.update("""
                        INSERT INTO dev_edge_event (
                            event_uid, tenant_id, organization_id,
                            deployment_id, edge_event_sequence,
                            event_type, delivery_class, schema_version,
                            target_type, target_stable_key_sha256,
                            device_occurred_at, clock_quality,
                            backend_received_at, payload_sha256,
                            canonical_sha256, source_inbox_id, created_at
                        ) VALUES (
                            ?, ?, ?,
                            ?, ?,
                            ?, ?, 1,
                            ?, ?,
                            ?, ?,
                            ?, ?,
                            ?, ?, ?
                        )
                        """,
                event.eventUid(),
                tenantId,
                organizationId,
                deployment.deploymentId(),
                event.sequence(),
                event.eventType(),
                event.deliveryClass(),
                event.targetType(),
                sha256(event.targetUid()),
                event.occurredAt(),
                event.clockQuality(),
                now,
                HexFormat.of().parseHex(event.payloadSha256()),
                HexFormat.of().parseHex(event.canonicalSha256()),
                inboxId,
                now);
        requireSingle(inserted, "insert trusted Orange Pi edge event");
        Long id = jdbc.queryForObject("""
                        SELECT id
                        FROM dev_edge_event
                        WHERE event_uid = ?
                        """,
                Long.class,
                event.eventUid());
        if (id == null) {
            throw new IllegalStateException(
                    "inserted Orange Pi edge event cannot be found");
        }
        return new EdgeInsert(id, true, false, now);
    }

    private void applyRuntimeSnapshot(
            ParsedEvent event,
            DeploymentTarget deployment,
            long edgeEventId,
            long tenantId,
            long organizationId,
            LocalDateTime now) {
        JsonNode payload = event.payload();
        JsonNode ports = requiredArray(payload, "ports");
        Map<Integer, Long> portIds = loadPortIds(
                deployment.deploymentId(),
                tenantId,
                organizationId);
        if (ports.size() != deployment.portCount()
                || portIds.size() != deployment.portCount()) {
            throw new UntrustedInboxSourceException(
                    "runtime snapshot does not cover the deployed port set");
        }
        Set<Integer> seen = new HashSet<>();
        List<JsonNode> normalizedPorts = new ArrayList<>();
        for (JsonNode port : ports) {
            int portNo = positiveInt(port, "portNo");
            if (!seen.add(portNo) || !portIds.containsKey(portNo)) {
                throw new UntrustedInboxSourceException(
                        "runtime snapshot port set is not authoritative");
            }
            normalizedPorts.add(port);
        }

        JsonNode applied = payload.get("appliedConfig");
        Long configVersion = null;
        byte[] configContent = null;
        byte[] configMcuPayload = null;
        if (applied != null && !applied.isNull()) {
            configVersion = positiveLong(applied, "version");
            configContent = digest(applied, "contentSha256");
            configMcuPayload = digest(
                    applied, "mcuPayloadSha256");
        }
        String uartState = requiredText(payload, "uartState");
        String mcuLink = switch (uartState) {
            case "READY", "NEGOTIATING" -> "ONLINE";
            case "INCOMPATIBLE" -> "INCOMPATIBLE";
            case "DISCONNECTED", "FAULT" -> "OFFLINE";
            default -> "UNKNOWN";
        };
        String localStorage = requiredText(
                payload, "localStorageState");
        String localStorageHealth = switch (localStorage) {
            case "HEALTHY" -> "OK";
            case "DEGRADED" -> "DEGRADED";
            case "READ_ONLY", "FULL", "CORRUPT" -> "FAILED";
            default -> "UNKNOWN";
        };
        String clockState = requiredText(payload, "clockState");
        String clockHealth = switch (clockState) {
            case "SYNCED" -> "OK";
            case "ESTIMATED" -> "DEGRADED";
            case "UNAVAILABLE" -> "FAILED";
            default -> "UNKNOWN";
        };
        String aggregateWeightHealth =
                aggregateWeightHealth(normalizedPorts);
        String capability = requiredText(
                payload, "capabilityBitmapHex");
        int updated = jdbc.update("""
                        UPDATE dev_deployment_runtime_state runtime
                        JOIN dev_device_deployment deployment
                          ON deployment.id = runtime.deployment_id
                        LEFT JOIN dev_device_transport_state transport
                          ON transport.asset_id = deployment.asset_id
                        SET runtime.edge_connection_status = CASE
                                WHEN transport.onenet_connection_status =
                                    'ONLINE' THEN 'ONLINE'
                                WHEN transport.onenet_connection_status =
                                    'OFFLINE' THEN 'OFFLINE'
                                ELSE 'UNKNOWN'
                            END,
                            runtime.mcu_link_status = ?,
                            runtime.aggregate_weight_health = ?,
                            runtime.local_storage_health = ?,
                            runtime.clock_sync_health = ?,
                            runtime.edge_boot_id = ?,
                            runtime.edge_software_version = ?,
                            runtime.mcu_firmware_version = ?,
                            runtime.mcu_boot_id = ?,
                            runtime.uart_state = ?,
                            runtime.uart_protocol_major = ?,
                            runtime.uart_protocol_minor = ?,
                            runtime.capability_bitmap_hex = ?,
                            runtime.local_storage_state = ?,
                            runtime.clock_state = ?,
                            runtime.pending_reliable_event_count = ?,
                            runtime.trusted_runtime_edge_event_id = ?,
                            runtime.trusted_runtime_edge_event_type =
                                'DEVICE_RUNTIME_SNAPSHOT',
                            runtime.trusted_runtime_sequence = ?,
                            runtime.trusted_runtime_received_at = ?,
                            runtime.orange_pi_reported_config_version_no = ?,
                            runtime.orange_pi_reported_config_content_sha256 = ?,
                            runtime.orange_pi_reported_config_mcu_payload_sha256 = ?,
                            runtime.last_heartbeat_at = ?,
                            runtime.last_device_event_at = ?,
                            runtime.lock_version = runtime.lock_version + 1,
                            runtime.updated_at = ?
                        WHERE runtime.tenant_id = ?
                          AND runtime.organization_id = ?
                          AND runtime.deployment_id = ?
                          AND (
                              runtime.trusted_runtime_sequence IS NULL
                              OR runtime.trusted_runtime_sequence < ?
                          )
                        """,
                mcuLink,
                aggregateWeightHealth,
                localStorageHealth,
                clockHealth,
                positiveLong(payload, "edgeBootId"),
                requiredText(payload, "edgeVersion"),
                nullableText(payload, "mcuFirmwareVersion"),
                nullableLong(payload, "mcuBootId"),
                uartState,
                nullableInteger(payload, "uartProtocolMajor"),
                nullableInteger(payload, "uartProtocolMinor"),
                capability,
                localStorage,
                clockState,
                nonNegativeLong(
                        payload, "pendingReliableEventCount"),
                edgeEventId,
                event.sequence(),
                now,
                configVersion,
                configContent,
                configMcuPayload,
                now,
                now,
                now,
                tenantId,
                organizationId,
                deployment.deploymentId(),
                event.sequence());
        if (updated == 0) {
            touchLastDeviceEvent(
                    deployment.deploymentId(),
                    tenantId,
                    organizationId,
                    now);
            return;
        }
        requireSingle(updated, "merge Orange Pi runtime snapshot");
        for (JsonNode port : normalizedPorts) {
            mergePortRuntime(
                    port,
                    portIds.get(positiveInt(port, "portNo")),
                    deployment.deploymentId(),
                    edgeEventId,
                    event.sequence(),
                    tenantId,
                    organizationId,
                    now);
        }
        refreshSafetyProjection(
                deployment.deploymentId(),
                tenantId,
                organizationId,
                now);
    }

    private CommandObservationResult applyCommandObservation(
            ParsedEvent event,
            DeploymentTarget deployment,
            long edgeEventId,
            long tenantId,
            long organizationId,
            LocalDateTime now) {
        JsonNode payload = event.payload();
        String observedType = requiredText(
                payload, "observedCommandType");
        String stage = requiredText(payload, "stage");
        List<CommandRow> commands = jdbc.query("""
                        SELECT
                            id, command_type, delivery_session_id,
                            physical_state
                        FROM dev_device_command
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND command_uid = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new CommandRow(
                        rs.getLong("id"),
                        rs.getString("command_type"),
                        nullableDatabaseLong(
                                rs, "delivery_session_id"),
                        rs.getString("physical_state")),
                tenantId,
                organizationId,
                deployment.deploymentId(),
                event.commandUid());
        if (commands.size() != 1
                || !commands.getFirst().commandType().equals(
                observedType)
                || !event.commandUid().equals(event.targetUid())) {
            throw new UntrustedInboxSourceException(
                    "observed device command is not authoritative");
        }
        CommandRow command = commands.getFirst();
        String mcuCommandUid = nullableText(
                payload, "mcuCommandUid");
        String errorCode = nullableText(payload, "errorCode");
        List<CommandStageRow> existing = jdbc.query(
                LOAD_COMMAND_STAGE_SQL,
                (rs, ignored) -> new CommandStageRow(
                        rs.getLong("edge_event_id"),
                        rs.getString("mcu_command_uid"),
                        rs.getString("error_code")),
                command.id(),
                stage);
        if (!existing.isEmpty()) {
            return new CommandObservationResult(null, true);
        }
        requireSingle(jdbc.update("""
                        INSERT INTO dev_device_command_event (
                            tenant_id, organization_id, deployment_id,
                            edge_event_id, edge_event_type,
                            command_id, delivery_session_id,
                            observed_command_type, observation_stage,
                            mcu_command_uid, error_code, created_at
                        ) VALUES (
                            ?, ?, ?,
                            ?, 'DEVICE_COMMAND_OBSERVED',
                            ?, ?,
                            ?, ?,
                            ?, ?, ?
                        )
                        """,
                tenantId,
                organizationId,
                deployment.deploymentId(),
                edgeEventId,
                command.id(),
                "START_DELIVERY_SESSION".equals(observedType)
                        ? command.deliverySessionId()
                        : null,
                observedType,
                stage,
                mcuCommandUid,
                errorCode,
                now),
                "insert device command observation");

        taskProofPort.completeDispatchFromTrustedCommandObservation(
                UUID.fromString(event.commandUid()));

        String desiredState = switch (stage) {
            case "RECEIVED", "ACCEPTED" -> "EDGE_ACCEPTED";
            case "MCU_ACCEPTED" -> "PHYSICAL_STARTED";
            case "REJECTED", "PRE_START_FAILED" ->
                    "PRE_START_FAILED";
            case "FAILED" -> "PHYSICAL_FAILED";
            default -> throw new IllegalArgumentException(
                    "device command stage is unsupported");
        };
        boolean shouldAdvance = shouldAdvanceCommand(
                command.physicalState(), desiredState);
        if (!shouldAdvance) {
            return new CommandObservationResult(
                    "NO_ACTION_REQUIRED", false);
        }
        requireSingle(jdbc.update("""
                        UPDATE dev_device_command
                        SET physical_state = ?,
                            edge_accepted_at =
                                COALESCE(edge_accepted_at, ?),
                            physical_started_at =
                                CASE
                                    WHEN ? IN (
                                        'PHYSICAL_STARTED',
                                        'PHYSICAL_FAILED'
                                    )
                                    THEN COALESCE(
                                        physical_started_at, ?)
                                    ELSE physical_started_at
                                END,
                            physical_ended_at =
                                CASE
                                    WHEN ? IN (
                                        'PRE_START_FAILED',
                                        'PHYSICAL_FAILED'
                                    )
                                    THEN COALESCE(
                                        physical_ended_at, ?)
                                    ELSE physical_ended_at
                                END,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                        """,
                desiredState,
                now,
                desiredState,
                now,
                desiredState,
                now,
                now,
                command.id(),
                tenantId,
                organizationId,
                deployment.deploymentId()),
                "advance observed device command");
        return new CommandObservationResult(
                "UPDATED", false);
    }

    private static boolean shouldAdvanceCommand(
            String current,
            String desired) {
        if (Set.of(
                "PHYSICAL_SUCCEEDED",
                "PHYSICAL_FAILED",
                "PRE_START_FAILED").contains(current)) {
            return false;
        }
        int currentRank = switch (current) {
            case "CREATED" -> 0;
            case "QUEUED" -> 1;
            case "EDGE_ACCEPTED" -> 2;
            case "PHYSICAL_STARTED" -> 3;
            default -> throw new IllegalStateException(
                    "device command has an unsupported state");
        };
        int desiredRank = switch (desired) {
            case "EDGE_ACCEPTED" -> 2;
            case "PHYSICAL_STARTED" -> 3;
            case "PRE_START_FAILED", "PHYSICAL_FAILED" -> 4;
            default -> throw new IllegalArgumentException(
                    "desired command state is unsupported");
        };
        return desiredRank > currentRank;
    }

    private void mergePortRuntime(
            JsonNode port,
            long portId,
            long deploymentId,
            long edgeEventId,
            long sequence,
            long tenantId,
            long organizationId,
            LocalDateTime now) {
        String cleanBasis = requiredText(
                port, "cleanDoorStateBasis");
        boolean closeConfirmed = requiredBoolean(
                port, "cleanerPhysicalCloseConfirmed");
        String cleanInferred =
                "CLEANER_CONFIRMATION".equals(cleanBasis)
                        && closeConfirmed
                        ? "CLOSED"
                        : "UNKNOWN";
        String weightHealth = legacyWeightHealth(
                requiredText(port, "weightSensorHealth"));
        String fullnessHealth = legacySensorHealth(
                requiredText(port, "fullnessSensorKind"),
                requiredText(port, "fullnessSensorValue"),
                requiredText(port, "fullnessSampleBasis"));
        String fullnessValue = "OK".equals(fullnessHealth)
                ? requiredText(port, "fullnessSensorValue")
                : "UNKNOWN";
        String smokeHealth = legacySmokeHealth(
                requiredText(port, "smokeSensorHealth"));
        String smokeState = "OK".equals(smokeHealth)
                ? requiredText(port, "smokeState")
                : "UNKNOWN";
        String portSafety = "ALARM".equals(smokeState)
                ? "SAFETY_BLOCKED"
                : "SAFE";
        jdbc.update("""
                        UPDATE dev_port_runtime_state
                        SET delivery_door_state = 'UNKNOWN',
                            delivery_door_actuator_health = ?,
                            delivery_door_contact_state = 'UNAVAILABLE',
                            last_delivery_door_command = ?,
                            last_delivery_door_output_status = ?,
                            delivery_door_physical_state_basis = ?,
                            clean_lock_power_state = ?,
                            clean_solenoid_health = ?,
                            clean_door_inferred_state = ?,
                            clean_door_state_basis = ?,
                            cleaner_physical_close_confirmed = ?,
                            weight_sensor_health = ?,
                            weight_measurement_uid = ?,
                            weight_measurement_status = ?,
                            weight_value_available = ?,
                            reported_weight_grams = ?,
                            weight_value_kind = ?,
                            weight_measurement_elapsed_ms = ?,
                            weight_sample_count = ?,
                            calibration_version = ?,
                            infrared_value = ?,
                            infrared_sensor_health = ?,
                            fullness_sensor_kind = ?,
                            fullness_sensor_value = ?,
                            fullness_sample_basis = ?,
                            representative_distance_mm = ?,
                            fullness_valid_sample_count = ?,
                            runtime_fault_bitmap = ?,
                            trusted_runtime_edge_event_id = ?,
                            trusted_runtime_edge_event_type =
                                'DEVICE_RUNTIME_SNAPSHOT',
                            trusted_runtime_sequence = ?,
                            last_observed_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND port_id = ?
                          AND (
                              trusted_runtime_sequence IS NULL
                              OR trusted_runtime_sequence < ?
                          )
                        """,
                "OUTPUT_REJECTED".equals(requiredText(
                        port, "lastDeliveryDoorOutputStatus"))
                        ? "ACTUATOR_FAULT"
                        : "UNKNOWN",
                requiredText(port, "lastDeliveryDoorCommand"),
                requiredText(
                        port, "lastDeliveryDoorOutputStatus"),
                requiredText(
                        port, "deliveryDoorPhysicalStateBasis"),
                requiredText(port, "cleanLockPowerState"),
                requiredText(port, "solenoidHealth"),
                cleanInferred,
                cleanBasis,
                closeConfirmed,
                weightHealth,
                nullableText(port, "weightMeasurementUid"),
                requiredText(port, "weightMeasurementStatus"),
                requiredBoolean(port, "weightValueAvailable"),
                nullableLong(port, "reportedWeightGrams"),
                requiredText(port, "weightValueKind"),
                nonNegativeLong(port, "measurementElapsedMs"),
                nonNegativeLong(port, "weightSampleCount"),
                nonNegativeLong(port, "calibrationVersion"),
                fullnessValue,
                fullnessHealth,
                requiredText(port, "fullnessSensorKind"),
                requiredText(port, "fullnessSensorValue"),
                requiredText(port, "fullnessSampleBasis"),
                nullableLong(port, "representativeDistanceMm"),
                nonNegativeLong(port, "fullnessValidSampleCount"),
                nonNegativeLong(port, "faultBitmap"),
                edgeEventId,
                sequence,
                now,
                now,
                tenantId,
                organizationId,
                deploymentId,
                portId,
                sequence);
        mergeSnapshotSafety(
                portId,
                edgeEventId,
                sequence,
                smokeState,
                smokeHealth,
                portSafety,
                tenantId,
                organizationId,
                deploymentId,
                now);
    }

    private void mergeSnapshotSafety(
            long portId,
            long edgeEventId,
            long sequence,
            String smokeState,
            String smokeHealth,
            String safetyStatus,
            long tenantId,
            long organizationId,
            long deploymentId,
            LocalDateTime now) {
        jdbc.update("""
                        UPDATE dev_port_runtime_state
                        SET smoke_state = ?,
                            smoke_sensor_health = ?,
                            safety_status = ?,
                            safety_projection_edge_event_id = ?,
                            safety_projection_edge_event_type =
                                'DEVICE_RUNTIME_SNAPSHOT',
                            safety_projection_sequence = ?,
                            last_observed_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND port_id = ?
                          AND (
                              safety_projection_sequence IS NULL
                              OR (
                                  safety_projection_edge_event_type =
                                      'DEVICE_RUNTIME_SNAPSHOT'
                                  AND safety_projection_sequence < ?
                              )
                          )
                        """,
                smokeState,
                smokeHealth,
                safetyStatus,
                edgeEventId,
                sequence,
                now,
                now,
                tenantId,
                organizationId,
                deploymentId,
                portId,
                sequence);
    }

    private FaultApplyResult observeFault(
            ParsedEvent event,
            DeploymentTarget deployment,
            long edgeEventId,
            long tenantId,
            long organizationId,
            LocalDateTime now) {
        JsonNode payload = event.payload();
        Long portId = resolvePortId(
                payload,
                deployment.deploymentId(),
                tenantId,
                organizationId);
        String component = requiredText(payload, "component");
        String faultCode = requiredText(payload, "faultCode");
        String impact = impact(payload);
        String faultUid = requiredText(payload, "faultUid");
        List<FaultRow> rows = loadFault(faultUid);
        String effect = "CREATED";
        if (rows.isEmpty()) {
            List<RecoveryObservation> recoveries =
                    loadRecovery(faultUid);
            if (!recoveries.isEmpty()) {
                RecoveryObservation recovery =
                        recoveries.getFirst();
                if (!recovery.matches(
                        deployment.deploymentId(),
                        portId,
                        component,
                        faultCode)
                        || impactRank(impact)
                        > impactRank(recovery.impact())) {
                    return FaultApplyResult.conflict(
                            "FAULT_RECOVERY_IDENTITY_CONFLICT",
                            "late fault observation conflicts with "
                                    + "the stored recovery evidence");
                }
                impact = strongerImpact(
                        impact, recovery.impact());
            }
            int inserted = jdbc.update("""
                            INSERT INTO dev_device_fault_event (
                                fault_uid, tenant_id, organization_id,
                                deployment_id, port_id, component_type,
                                fault_code, fault_key, impact_level, status,
                                first_source_kind,
                                first_source_edge_event_id,
                                first_source_edge_event_type,
                                first_source_evidence_sha256,
                                first_detected_at, last_detected_at,
                                discovery_count, recovery_source_kind,
                                recovery_source_edge_event_id,
                                recovery_source_edge_event_type,
                                recovery_method, recovery_audit_log_id,
                                recovered_at, recovered_by_staff_account_id,
                                recovery_reason,
                                device_recovery_observed_edge_event_id,
                                device_recovery_observed_edge_event_type,
                                device_recovery_observed_at,
                                lock_version, created_at
                            ) VALUES (
                                ?, ?, ?,
                                ?, ?, ?,
                                ?, ?, ?, 'OPEN',
                                'EDGE_EVENT',
                                ?,
                                'DEVICE_FAULT_OBSERVED',
                                ?,
                                ?, ?,
                                1, NULL,
                                NULL,
                                NULL,
                                NULL, NULL,
                                NULL, NULL,
                                NULL,
                                NULL,
                                NULL,
                                NULL,
                                0, ?
                            )
                            """,
                    faultUid,
                    tenantId,
                    organizationId,
                    deployment.deploymentId(),
                    portId,
                    component,
                    faultCode,
                    faultKey(
                            tenantId,
                            deployment.deploymentId(),
                            portId,
                            component,
                            faultCode),
                    impact,
                    edgeEventId,
                    HexFormat.of().parseHex(event.payloadSha256()),
                    now,
                    now,
                    now);
            requireSingle(inserted, "insert trusted device fault");
            rows = loadFault(faultUid);
        } else {
            FaultRow row = rows.getFirst();
            if (!sameFaultIdentity(
                    row, deployment.deploymentId(), portId,
                    component, faultCode)) {
                return FaultApplyResult.conflict(
                        "FAULT_IDENTITY_CONFLICT",
                        "fault uid carries another deployment, port, "
                                + "component or fault code");
            }
            if (!"OPEN".equals(row.status())
                    && impactRank(impact)
                    > impactRank(row.impact())) {
                return FaultApplyResult.conflict(
                        "FAULT_LIFECYCLE_CONFLICT",
                        "a recovered fault uid cannot be escalated or "
                                + "opened again");
            }
            if ("OPEN".equals(row.status())) {
                requireSingle(jdbc.update("""
                                UPDATE dev_device_fault_event
                                SET impact_level = ?,
                                    last_detected_at = ?,
                                    discovery_count =
                                        discovery_count + 1,
                                    lock_version = lock_version + 1
                                WHERE id = ?
                                """,
                        strongerImpact(row.impact(), impact),
                        now,
                        row.id()),
                        "merge repeated trusted device fault");
                rows = loadFault(faultUid);
            }
            effect = "UPDATED";
        }
        applyStoredRecoveryIfPresent(
                rows.getFirst(), faultUid, now);
        refreshSafetyProjection(
                deployment.deploymentId(),
                tenantId,
                organizationId,
                now);
        return FaultApplyResult.applied(effect);
    }

    private FaultApplyResult observeFaultRecovery(
            ParsedEvent event,
            DeploymentTarget deployment,
            long edgeEventId,
            long tenantId,
            long organizationId,
            LocalDateTime now) {
        JsonNode payload = event.payload();
        Long portId = resolvePortId(
                payload,
                deployment.deploymentId(),
                tenantId,
                organizationId);
        String component = requiredText(payload, "component");
        String faultCode = requiredText(payload, "faultCode");
        String impact = impact(payload);
        String faultUid = requiredText(payload, "faultUid");
        List<RecoveryObservation> existingRecoveries =
                loadRecovery(faultUid);
        if (!existingRecoveries.isEmpty()) {
            return FaultApplyResult.conflict(
                    "FAULT_RECOVERY_IDENTITY_CONFLICT",
                    "fault uid already has recovery evidence from "
                            + "another event");
        }
        List<FaultRow> faults = loadFault(faultUid);
        if (!faults.isEmpty()) {
            FaultRow fault = faults.getFirst();
            if (!sameFaultIdentity(
                    fault, deployment.deploymentId(), portId,
                    component, faultCode)) {
                return FaultApplyResult.conflict(
                        "FAULT_RECOVERY_IDENTITY_CONFLICT",
                        "fault recovery does not identify the same "
                                + "deployment, port, component and code");
            }
            if (impactRank(impact) < impactRank(fault.impact())) {
                return FaultApplyResult.conflict(
                        "FAULT_RECOVERY_SEVERITY_CONFLICT",
                        "fault recovery reports a lower severity than "
                                + "the stored fault maximum");
            }
            if ("OPEN".equals(fault.status())
                    && impactRank(impact)
                    > impactRank(fault.impact())) {
                requireSingle(jdbc.update("""
                                UPDATE dev_device_fault_event
                                SET impact_level = ?,
                                    lock_version = lock_version + 1
                                WHERE id = ?
                                """,
                        impact,
                        fault.id()),
                        "merge recovery maximum fault severity");
                faults = loadFault(faultUid);
            }
        }
        int inserted = jdbc.update("""
                        INSERT INTO dev_fault_recovery_observation (
                            fault_uid, tenant_id, organization_id,
                            deployment_id, port_id, component_type,
                            fault_code, impact_level,
                            source_edge_event_id,
                            source_edge_event_type,
                            observed_at, created_at
                        ) VALUES (
                            ?, ?, ?,
                            ?, ?, ?,
                            ?, ?,
                            ?,
                            'DEVICE_FAULT_RECOVERED',
                            ?, ?
                        )
                        """,
                faultUid,
                tenantId,
                organizationId,
                deployment.deploymentId(),
                portId,
                component,
                faultCode,
                impact,
                edgeEventId,
                now,
                now);
        requireSingle(inserted, "store trusted fault recovery evidence");
        if (!faults.isEmpty()) {
            FaultRow fault = faults.getFirst();
            applyRecovery(
                    fault,
                    edgeEventId,
                    now);
        }
        refreshSafetyProjection(
                deployment.deploymentId(),
                tenantId,
                organizationId,
                now);
        return FaultApplyResult.applied(
                faults.isEmpty() ? "CREATED" : "UPDATED");
    }

    private void applyStoredRecoveryIfPresent(
            FaultRow fault,
            String faultUid,
            LocalDateTime now) {
        List<RecoveryObservation> rows = loadRecovery(faultUid);
        if (!rows.isEmpty()) {
            RecoveryObservation observation = rows.getFirst();
            applyRecovery(
                    fault,
                    observation.edgeEventId(),
                    now);
        }
    }

    private void applyRecovery(
            FaultRow fault,
            long recoveryEdgeEventId,
            LocalDateTime recoveredAt) {
        if ("SAFETY_BLOCKING".equals(fault.impact())) {
            jdbc.update("""
                            UPDATE dev_device_fault_event
                            SET device_recovery_observed_edge_event_id = ?,
                                device_recovery_observed_edge_event_type =
                                    'DEVICE_FAULT_RECOVERED',
                                device_recovery_observed_at = ?,
                                lock_version = lock_version + 1
                            WHERE id = ?
                              AND (
                                  device_recovery_observed_edge_event_id
                                      IS NULL
                                  OR
                                  device_recovery_observed_edge_event_id = ?
                              )
                            """,
                    recoveryEdgeEventId,
                    recoveredAt,
                    fault.id(),
                    recoveryEdgeEventId);
            return;
        }
        if (!"OPEN".equals(fault.status())) {
            return;
        }
        requireSingle(jdbc.update("""
                        UPDATE dev_device_fault_event
                        SET status = 'RECOVERED',
                            recovery_source_kind = 'EDGE_EVENT',
                            recovery_source_edge_event_id = ?,
                            recovery_source_edge_event_type =
                                'DEVICE_FAULT_RECOVERED',
                            recovery_method = 'DEVICE_REPORTED',
                            recovered_at = ?,
                            device_recovery_observed_edge_event_id = ?,
                            device_recovery_observed_edge_event_type =
                                'DEVICE_FAULT_RECOVERED',
                            device_recovery_observed_at = ?,
                            lock_version = lock_version + 1
                        WHERE id = ?
                        """,
                recoveryEdgeEventId,
                recoveredAt,
                recoveryEdgeEventId,
                recoveredAt,
                fault.id()),
                "apply trusted non-safety fault recovery");
    }

    private void applySafetyChange(
            ParsedEvent event,
            DeploymentTarget deployment,
            long edgeEventId,
            long tenantId,
            long organizationId,
            LocalDateTime now) {
        JsonNode payload = event.payload();
        Long portId = resolvePortId(
                payload,
                deployment.deploymentId(),
                tenantId,
                organizationId);
        String reportedHealth = requiredText(
                payload, "smokeSensorHealth");
        String legacyHealth = legacySmokeHealth(reportedHealth);
        String reportedState = requiredText(payload, "smokeState");
        String legacyState = "OK".equals(legacyHealth)
                ? reportedState
                : "UNKNOWN";
        String safety = "ALARM".equals(reportedState)
                ? "SAFETY_BLOCKED"
                : payload.get("faultCode") != null
                && !payload.get("faultCode").isNull()
                ? "OPERATION_BLOCKED"
                : "SAFE";
        if (portId == null) {
            jdbc.update("""
                            UPDATE dev_port_runtime_state
                            SET smoke_state = ?,
                                smoke_sensor_health = ?,
                                safety_status = ?,
                                safety_projection_edge_event_id = ?,
                                safety_projection_edge_event_type =
                                    'SAFETY_SENSOR_STATE_CHANGED',
                                safety_projection_sequence = ?,
                                last_observed_at = ?,
                                lock_version = lock_version + 1,
                                updated_at = ?
                            WHERE tenant_id = ?
                              AND organization_id = ?
                              AND deployment_id = ?
                              AND (
                                  safety_projection_sequence IS NULL
                                  OR safety_projection_sequence < ?
                              )
                            """,
                    legacyState,
                    legacyHealth,
                    safety,
                    edgeEventId,
                    event.sequence(),
                    now,
                    now,
                    tenantId,
                    organizationId,
                    deployment.deploymentId(),
                    event.sequence());
        } else {
            jdbc.update("""
                            UPDATE dev_port_runtime_state
                            SET smoke_state = ?,
                                smoke_sensor_health = ?,
                                safety_status = ?,
                                safety_projection_edge_event_id = ?,
                                safety_projection_edge_event_type =
                                    'SAFETY_SENSOR_STATE_CHANGED',
                                safety_projection_sequence = ?,
                                last_observed_at = ?,
                                lock_version = lock_version + 1,
                                updated_at = ?
                            WHERE tenant_id = ?
                              AND organization_id = ?
                              AND deployment_id = ?
                              AND port_id = ?
                              AND (
                                  safety_projection_sequence IS NULL
                                  OR safety_projection_sequence < ?
                              )
                            """,
                    legacyState,
                    legacyHealth,
                    safety,
                    edgeEventId,
                    event.sequence(),
                    now,
                    now,
                    tenantId,
                    organizationId,
                    deployment.deploymentId(),
                    portId,
                    event.sequence());
        }
        jdbc.update("""
                        UPDATE dev_deployment_runtime_state
                        SET safety_projection_edge_event_id = ?,
                            safety_projection_edge_event_type =
                                'SAFETY_SENSOR_STATE_CHANGED',
                            safety_projection_sequence = ?,
                            last_device_event_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND (
                              safety_projection_sequence IS NULL
                              OR safety_projection_sequence < ?
                          )
                        """,
                edgeEventId,
                event.sequence(),
                now,
                now,
                tenantId,
                organizationId,
                deployment.deploymentId(),
                event.sequence());
        refreshSafetyProjection(
                deployment.deploymentId(),
                tenantId,
                organizationId,
                now);
    }

    private void applyConfirmationReceipt(
            ParsedEvent event,
            DeploymentTarget deployment) {
        JsonNode receipt = event.payload();
        String confirmationUid = requiredText(
                receipt, "confirmationUid");
        List<ConfirmationTarget> rows = jdbc.query("""
                        SELECT
                            task.state,
                            CAST(
                                task.redacted_execution_snapshot AS CHAR
                            )
                                semantic_payload
                        FROM ops_reliable_task task
                        WHERE task.task_type = 'CONFIRM_EDGE_EVENT'
                          AND task.target_type =
                              'BUSINESS_CONFIRMATION'
                          AND task.target_stable_key = ?
                          AND task.source_device_deployment_id = ?
                          AND task.source_device_command_id IS NULL
                        FOR UPDATE
                        """,
                (rs, ignored) -> new ConfirmationTarget(
                        rs.getString("state"),
                        rs.getString("semantic_payload")),
                confirmationUid,
                deployment.deploymentId());
        if (rows.size() != 1) {
            throw new UntrustedInboxSourceException(
                    "confirmation receipt target is not authoritative");
        }
        ConfirmationTarget target = rows.getFirst();
        JsonNode envelope = objectMapper.readTree(
                target.semanticEnvelope());
        JsonNode expected = requiredObject(envelope, "payload");
        boolean matches = requiredText(
                envelope, "commandUid").equals(event.commandUid())
                && confirmationUid.equals(
                requiredText(expected, "confirmationUid"))
                && requiredText(receipt, "originalEventUid")
                .equals(requiredText(expected, "originalEventUid"))
                && requiredText(receipt, "originalPayloadSha256")
                .equals(requiredText(
                        expected, "originalPayloadSha256"))
                && requiredText(receipt, "outcome")
                .equals(requiredText(expected, "outcome"));
        if (!matches) {
            throw new UntrustedInboxSourceException(
                    "confirmation receipt differs from the frozen command");
        }
        taskProofPort.completeFromTrustedProof(
                ReliableEdgeConfirmationService.TASK_TYPE,
                ReliableEdgeConfirmationService.TARGET_TYPE,
                confirmationUid);
    }

    private void refreshSafetyProjection(
            long deploymentId,
            long tenantId,
            long organizationId,
            LocalDateTime now) {
        Integer safetyFaults = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_device_fault_event
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND status = 'OPEN'
                          AND impact_level = 'SAFETY_BLOCKING'
                        """,
                Integer.class,
                tenantId,
                organizationId,
                deploymentId);
        Integer businessFaults = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_device_fault_event
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND status = 'OPEN'
                          AND impact_level = 'BUSINESS_BLOCKING'
                          AND fault_code <> 'INITIAL_COMMISSIONING'
                        """,
                Integer.class,
                tenantId,
                organizationId,
                deploymentId);
        Integer safetyPorts = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_port_runtime_state
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND safety_status = 'SAFETY_BLOCKED'
                        """,
                Integer.class,
                tenantId,
                organizationId,
                deploymentId);
        Integer blockedPorts = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_port_runtime_state
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND safety_status = 'OPERATION_BLOCKED'
                        """,
                Integer.class,
                tenantId,
                organizationId,
                deploymentId);
        String status = positive(safetyFaults)
                || positive(safetyPorts)
                ? "SAFETY_BLOCKED"
                : positive(businessFaults)
                || positive(blockedPorts)
                ? "OPERATION_BLOCKED"
                : "SAFE";
        requireSingle(jdbc.update("""
                        UPDATE dev_deployment_runtime_state
                        SET safety_status = ?,
                            last_device_event_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                        """,
                status,
                now,
                now,
                tenantId,
                organizationId,
                deploymentId),
                "refresh Orange Pi safety projection");
    }

    private Map<Integer, Long> loadPortIds(
            long deploymentId,
            long tenantId,
            long organizationId) {
        Map<Integer, Long> result = new HashMap<>();
        jdbc.query("""
                        SELECT id, port_no
                        FROM dev_port
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                        """,
                rs -> {
                    result.put(
                            rs.getInt("port_no"),
                            rs.getLong("id"));
                },
                tenantId,
                organizationId,
                deploymentId);
        return result;
    }

    private Long resolvePortId(
            JsonNode payload,
            long deploymentId,
            long tenantId,
            long organizationId) {
        Long portNo = nullableLong(payload, "portNo");
        if (portNo == null) {
            return null;
        }
        List<Long> rows = jdbc.query("""
                        SELECT id
                        FROM dev_port
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND port_no = ?
                        """,
                (rs, ignored) -> rs.getLong("id"),
                tenantId,
                organizationId,
                deploymentId,
                portNo);
        if (rows.size() != 1) {
            throw new UntrustedInboxSourceException(
                    "event port is not part of the deployment");
        }
        return rows.getFirst();
    }

    private List<FaultRow> loadFault(String faultUid) {
        return jdbc.query("""
                        SELECT
                            id, deployment_id, port_id,
                            component_type, fault_code,
                            impact_level, status
                        FROM dev_device_fault_event
                        WHERE fault_uid = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new FaultRow(
                        rs.getLong("id"),
                        rs.getLong("deployment_id"),
                        nullableDatabaseLong(rs, "port_id"),
                        rs.getString("component_type"),
                        rs.getString("fault_code"),
                        rs.getString("impact_level"),
                        rs.getString("status")),
                faultUid);
    }

    private List<RecoveryObservation> loadRecovery(
            String faultUid) {
        return jdbc.query("""
                        SELECT
                            source_edge_event_id, deployment_id,
                            port_id, component_type, fault_code,
                            impact_level
                        FROM dev_fault_recovery_observation
                        WHERE fault_uid = ?
                        """,
                (rs, ignored) -> new RecoveryObservation(
                        rs.getLong("source_edge_event_id"),
                        rs.getLong("deployment_id"),
                        nullableDatabaseLong(rs, "port_id"),
                        rs.getString("component_type"),
                        rs.getString("fault_code"),
                        rs.getString("impact_level")),
                faultUid);
    }

    private static boolean sameFaultIdentity(
            FaultRow row,
            long deploymentId,
            Long portId,
            String component,
            String faultCode) {
        return row.deploymentId() == deploymentId
                && java.util.Objects.equals(row.portId(), portId)
                && row.component().equals(component)
                && row.faultCode().equals(faultCode);
    }

    private static String strongerImpact(
            String left, String right) {
        return impactRank(left) >= impactRank(right)
                ? left
                : right;
    }

    private static int impactRank(String impact) {
        return switch (impact) {
            case "DEGRADED" -> 1;
            case "BUSINESS_BLOCKING" -> 2;
            case "SAFETY_BLOCKING" -> 3;
            default -> throw new IllegalArgumentException(
                    "fault impact is unsupported");
        };
    }

    private ParsedEvent parse(
            String expectedKind, String normalizedPayload) {
        JsonNode root = objectMapper.readTree(normalizedPayload);
        JsonNode source = requiredObject(root, "trustedSource");
        JsonNode event = requiredObject(root, "event");
        JsonNode target = requiredObject(event, "target");
        String eventType = requiredText(event, "eventType");
        if (!expectedKind.equals(eventType)
                || event.path("schemaVersion").asInt() != 1) {
            throw new IllegalArgumentException(
                    "trusted Orange Pi envelope constants differ");
        }
        String delivery = requiredText(event, "deliveryClass");
        String targetType = requiredText(target, "type");
        String expectedDelivery =
                "DEVICE_RUNTIME_SNAPSHOT".equals(eventType)
                        ? "TELEMETRY_SNAPSHOT"
                        : "BUSINESS_CONFIRMATION_RECEIPT".equals(
                        eventType)
                        ? "CONTROL_RECEIPT"
                        : "RELIABLE_FACT";
        String expectedTarget =
                "BUSINESS_CONFIRMATION_RECEIPT".equals(eventType)
                        ? "BUSINESS_CONFIRMATION"
                        : "DEVICE_COMMAND_OBSERVED".equals(eventType)
                        ? "DEVICE_COMMAND"
                        : Set.of(
                                "PHOTO_STATUS_REPORTED",
                                "PHOTO_UPLOAD_GRANT_REQUESTED")
                        .contains(eventType)
                        ? requiredText(
                                requiredObject(event, "payload"),
                                "workType")
                        : "DEVICE_DEPLOYMENT";
        if (!expectedDelivery.equals(delivery)
                || !expectedTarget.equals(targetType)) {
            throw new IllegalArgumentException(
                    "trusted Orange Pi envelope class differs");
        }
        String deploymentCode = requiredText(
                event, "deploymentCode");
        if ("DEVICE_DEPLOYMENT".equals(targetType)
                && !deploymentCode.equals(
                requiredText(target, "uid"))) {
            throw new IllegalArgumentException(
                    "runtime target differs from deployment");
        }
        if (Set.of(
                "PHOTO_STATUS_REPORTED",
                "PHOTO_UPLOAD_GRANT_REQUESTED").contains(eventType)) {
            JsonNode payload = requiredObject(event, "payload");
            if (!requiredText(payload, "workUid").equals(
                    requiredText(target, "uid"))
                    || nullableText(event, "commandUid") != null) {
                throw new IllegalArgumentException(
                        "photo target differs from payload");
            }
        }
        return new ParsedEvent(
                requiredText(source, "deviceName"),
                requiredText(event, "eventUid"),
                deploymentCode,
                positiveLong(event, "edgeEventSequence"),
                eventType,
                delivery,
                targetType,
                requiredText(target, "uid"),
                parseOccurredAt(
                        event.get("occurredAt"),
                        requiredText(event, "clockQuality")),
                requiredText(event, "clockQuality"),
                nullableText(event, "commandUid"),
                requiredText(event, "payloadSha256"),
                requiredText(root, "eventCanonicalSha256"),
                requiredObject(event, "payload"));
    }

    private static TrustedPhotoStatusFact photoStatusFact(
            ParsedEvent event,
            DeploymentTarget deployment,
            long edgeEventId,
            long tenantId,
            long organizationId,
            LocalDateTime receivedAt) {
        JsonNode payload = event.payload();
        JsonNode photo = requiredObject(payload, "photo");
        String workType = requiredText(payload, "workType");
        String status = requiredText(photo, "status");
        UUID photoUid = nullableUuid(photo, "photoUid");
        String objectUrl = nullableText(photo, "url");
        byte[] photoSha256 = nullableDigest(photo, "sha256");
        Long sizeBytes = nullableLong(photo, "sizeBytes");
        LocalDateTime capturedAt =
                nullableInstant(photo, "capturedAt");
        String missingReason = nullableText(
                photo, "missingReason");
        boolean valid = switch (status) {
            case "AVAILABLE" ->
                    photoUid != null
                            && objectUrl != null
                            && photoSha256 != null
                            && sizeBytes != null
                            && sizeBytes > 0
                            && capturedAt != null
                            && missingReason == null;
            case "PERMANENTLY_MISSING" ->
                    objectUrl == null
                            && missingReason != null
                            && (
                            photoUid == null
                                    && photoSha256 == null
                                    && sizeBytes == null
                                    && capturedAt == null
                            || photoUid != null
                                    && photoSha256 != null
                                    && sizeBytes != null
                                    && sizeBytes > 0
                                    && capturedAt != null);
            default -> false;
        };
        if (!valid) {
            throw new IllegalArgumentException(
                    "photo terminal fact has an invalid state");
        }
        return new TrustedPhotoStatusFact(
                tenantId,
                organizationId,
                deployment.deploymentId(),
                edgeEventId,
                UUID.fromString(requiredText(payload, "workUid")),
                workType,
                requiredText(photo, "slot"),
                status,
                photoUid,
                objectUrl,
                photoSha256,
                sizeBytes,
                capturedAt,
                missingReason,
                event.occurredAt(),
                receivedAt);
    }

    private static LocalDateTime parseOccurredAt(
            JsonNode node, String clockQuality) {
        if ("SYNCED".equals(clockQuality)) {
            if (node == null || !node.isTextual()) {
                throw new IllegalArgumentException(
                        "synced event lacks occurredAt");
            }
            return LocalDateTime.ofInstant(
                    Instant.parse(node.asText()), ZoneOffset.UTC);
        }
        if (node != null && !node.isNull()) {
            throw new IllegalArgumentException(
                    "unsynced event carries occurredAt");
        }
        return null;
    }

    private static String aggregateWeightHealth(
            List<JsonNode> ports) {
        boolean allOk = true;
        boolean failed = false;
        for (JsonNode port : ports) {
            String health = requiredText(
                    port, "weightSensorHealth");
            allOk &= "OK".equals(health);
            failed |= Set.of(
                    "SENSOR_FAULT",
                    "DISCONNECTED",
                    "PROTOCOL_ERROR",
                    "CONFIG_ERROR",
                    "OVERLOAD").contains(health);
        }
        return allOk ? "OK" : failed ? "FAILED" : "DEGRADED";
    }

    private static String legacyWeightHealth(String value) {
        return switch (value) {
            case "OK" -> "OK";
            case "TIMEOUT" -> "TIMEOUT";
            case "DISCONNECTED" -> "DISCONNECTED";
            case "UNKNOWN" -> "UNKNOWN";
            default -> "SENSOR_FAULT";
        };
    }

    private static String legacySensorHealth(
            String sensorKind,
            String fullnessValue,
            String sampleBasis) {
        if ("NOT_SAMPLED".equals(sampleBasis)) {
            return "DIGITAL_INFRARED".equals(sensorKind)
                    && Set.of("CLEAR", "BLOCKED")
                    .contains(fullnessValue)
                    ? "OK"
                    : "UNKNOWN";
        }
        return Set.of("CLEAR", "BLOCKED").contains(fullnessValue)
                ? "OK"
                : "SENSOR_FAULT";
    }

    private static String legacySmokeHealth(String value) {
        return switch (value) {
            case "OK" -> "OK";
            case "DISCONNECTED" -> "DISCONNECTED";
            case "UNKNOWN" -> "UNKNOWN";
            default -> "SENSOR_FAULT";
        };
    }

    private static String impact(JsonNode payload) {
        String severity = requiredText(payload, "severity");
        String component = requiredText(payload, "component");
        return switch (severity) {
            case "WARNING" -> "DEGRADED";
            case "BLOCK_PORT" -> "BUSINESS_BLOCKING";
            case "BLOCK_DEVICE" ->
                    "SMOKE_SENSOR".equals(component)
                            ? "SAFETY_BLOCKING"
                            : "BUSINESS_BLOCKING";
            default -> throw new IllegalArgumentException(
                    "fault severity is unsupported");
        };
    }

    private static byte[] faultKey(
            long tenantId,
            long deploymentId,
            Long portId,
            String component,
            String faultCode) {
        return sha256(
                tenantId + "\0"
                        + deploymentId + "\0"
                        + (portId == null ? "-" : portId) + "\0"
                        + component + "\0"
                        + faultCode);
    }

    private static byte[] digest(JsonNode node, String field) {
        return HexFormat.of().parseHex(
                requiredText(node, field));
    }

    private static byte[] sha256(String value) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(
                    value.getBytes(StandardCharsets.UTF_8));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException(
                    "SHA-256 is unavailable", exception);
        }
    }

    private LocalDateTime databaseNow() {
        return jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
    }

    private void touchLastDeviceEvent(
            long deploymentId,
            long tenantId,
            long organizationId,
            LocalDateTime now) {
        requireSingle(jdbc.update("""
                        UPDATE dev_deployment_runtime_state
                        SET last_device_event_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                        """,
                now,
                now,
                tenantId,
                organizationId,
                deploymentId),
                "touch older Orange Pi event");
    }

    private static boolean positive(Integer value) {
        return value != null && value > 0;
    }

    private static JsonNode requiredObject(
            JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
        if (value == null || !value.isObject()) {
            throw new IllegalArgumentException(
                    field + " must be an object");
        }
        return value;
    }

    private static JsonNode requiredArray(
            JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
        if (value == null || !value.isArray()) {
            throw new IllegalArgumentException(
                    field + " must be an array");
        }
        return value;
    }

    private static String requiredText(
            JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
        if (value == null || !value.isTextual()
                || value.asText().isBlank()) {
            throw new IllegalArgumentException(
                    field + " must be text");
        }
        return value.asText();
    }

    private static String nullableText(
            JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
        if (value == null || value.isNull()) {
            return null;
        }
        if (!value.isTextual()) {
            throw new IllegalArgumentException(
                    field + " must be nullable text");
        }
        return value.asText();
    }

    private static UUID nullableUuid(
            JsonNode node, String field) {
        String value = nullableText(node, field);
        if (value == null) {
            return null;
        }
        UUID parsed;
        try {
            parsed = UUID.fromString(value);
        } catch (IllegalArgumentException exception) {
            throw new IllegalArgumentException(
                    field + " must be a UUID", exception);
        }
        if (parsed.version() != 4
                || parsed.variant() != 2
                || !parsed.toString().equals(value)) {
            throw new IllegalArgumentException(
                    field + " must be a lowercase UUIDv4");
        }
        return parsed;
    }

    private static byte[] nullableDigest(
            JsonNode node, String field) {
        String value = nullableText(node, field);
        if (value == null) {
            return null;
        }
        if (!value.matches("[0-9a-f]{64}")) {
            throw new IllegalArgumentException(
                    field + " must be a lowercase SHA-256");
        }
        return HexFormat.of().parseHex(value);
    }

    private static LocalDateTime nullableInstant(
            JsonNode node, String field) {
        String value = nullableText(node, field);
        if (value == null) {
            return null;
        }
        try {
            return LocalDateTime.ofInstant(
                    Instant.parse(value), ZoneOffset.UTC);
        } catch (java.time.format.DateTimeParseException exception) {
            throw new IllegalArgumentException(
                    field + " must be an RFC3339 instant",
                    exception);
        }
    }

    private static long positiveLong(
            JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
        long parsed = exactLong(
                value, field, "a positive integer");
        if (parsed <= 0) {
            throw new IllegalArgumentException(
                    field + " must be a positive integer");
        }
        return parsed;
    }

    private static long nonNegativeLong(
            JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
        long parsed = exactLong(
                value, field, "a non-negative integer");
        if (parsed < 0) {
            throw new IllegalArgumentException(
                    field + " must be a non-negative integer");
        }
        return parsed;
    }

    private static int positiveInt(
            JsonNode node, String field) {
        return Math.toIntExact(positiveLong(node, field));
    }

    private static Long nullableLong(
            JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
        if (value == null || value.isNull()) {
            return null;
        }
        return exactLong(
                value, field, "a nullable integer");
    }

    private static long exactLong(
            JsonNode value,
            String field,
            String expectation) {
        if (value == null || !value.isNumber()) {
            throw new IllegalArgumentException(
                    field + " must be " + expectation);
        }
        try {
            return value.decimalValue().longValueExact();
        } catch (ArithmeticException exception) {
            throw new IllegalArgumentException(
                    field + " must be " + expectation,
                    exception);
        }
    }

    private static Integer nullableInteger(
            JsonNode node, String field) {
        Long value = nullableLong(node, field);
        return value == null ? null : Math.toIntExact(value);
    }

    private static boolean requiredBoolean(
            JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
        if (value == null || !value.isBoolean()) {
            throw new IllegalArgumentException(
                    field + " must be a boolean");
        }
        return value.booleanValue();
    }

    private static Long nullableDatabaseLong(
            java.sql.ResultSet resultSet,
            String field) throws java.sql.SQLException {
        long value = resultSet.getLong(field);
        return resultSet.wasNull() ? null : value;
    }

    private static void requireSingle(int updated, String operation) {
        if (updated != 1) {
            throw new IllegalStateException(
                    operation + " updated " + updated + " rows");
        }
    }

    private record ParsedEvent(
            String hardwareSn,
            String eventUid,
            String deploymentCode,
            long sequence,
            String eventType,
            String deliveryClass,
            String targetType,
            String targetUid,
            LocalDateTime occurredAt,
            String clockQuality,
            String commandUid,
            String payloadSha256,
            String canonicalSha256,
            JsonNode payload) {
    }

    private record DeploymentTarget(
            long assetId,
            long deploymentId,
            int portCount) {
    }

    private record LockedAsset(
            long assetId,
            int portCount) {
    }

    private record ExistingEdge(
            long id,
            String eventUid,
            long sequence,
            String canonicalSha256,
            long inboxId) {

        private boolean matches(
                ParsedEvent event, long expectedInboxId) {
            return eventUid.equals(event.eventUid())
                    && sequence == event.sequence()
                    && canonicalSha256.equals(
                    event.canonicalSha256())
                    && inboxId == expectedInboxId;
        }
    }

    private record EdgeInsert(
            long eventId,
            boolean inserted,
            boolean quarantined,
            LocalDateTime receivedAt) {
    }

    private record FaultRow(
            long id,
            long deploymentId,
            Long portId,
            String component,
            String faultCode,
            String impact,
            String status) {
    }

    private record RecoveryObservation(
            long edgeEventId,
            long deploymentId,
            Long portId,
            String component,
            String faultCode,
            String impact) {

        private boolean matches(
                long expectedDeploymentId,
                Long expectedPortId,
                String expectedComponent,
                String expectedFaultCode) {
            return deploymentId == expectedDeploymentId
                    && java.util.Objects.equals(
                    portId, expectedPortId)
                    && component.equals(expectedComponent)
                    && faultCode.equals(expectedFaultCode);
        }
    }

    private record ConfirmationTarget(
            String taskState,
            String semanticEnvelope) {
    }

    private record CommandRow(
            long id,
            String commandType,
            Long deliverySessionId,
            String physicalState) {
    }

    private record CommandStageRow(
            long edgeEventId,
            String mcuCommandUid,
            String errorCode) {
    }

    private record CommandObservationResult(
            String effectKind,
            boolean conflict) {
    }

    private record FaultApplyResult(
            String effectKind,
            String conflictCode,
            String detail) {

        private static FaultApplyResult applied(
                String effectKind) {
            return new FaultApplyResult(
                    effectKind, null, null);
        }

        private static FaultApplyResult conflict(
                String conflictCode,
                String detail) {
            return new FaultApplyResult(
                    null, conflictCode, detail);
        }

        private boolean conflict() {
            return conflictCode != null;
        }
    }
}
