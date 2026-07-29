package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;
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

/**
 * Applies trusted runtime facts reported by the Orange Pi through OneNet.
 *
 * <p>MCU and UART fields are retained as diagnostics. They are deliberately
 * not interpreted as backend activation gates.</p>
 */
@Service
public class TrustedOrangePiRuntimeFactService {

    private static final Set<String> SUPPORTED = Set.of(
            "DEVICE_RUNTIME_SNAPSHOT",
            "DEVICE_FAULT_OBSERVED",
            "DEVICE_FAULT_RECOVERED",
            "SAFETY_SENSOR_STATE_CHANGED",
            "BUSINESS_CONFIRMATION_RECEIPT");

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final ReliableEdgeConfirmationService confirmationService;
    private final ReliableDeviceTaskProofPort taskProofPort;
    private final TrustedInboxQuarantinePort quarantinePort;
    private final TrustedOrganizationInboxRefFactory inboxRefFactory;

    public TrustedOrangePiRuntimeFactService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            ReliableEdgeConfirmationService confirmationService,
            ReliableDeviceTaskProofPort taskProofPort,
            TrustedInboxQuarantinePort quarantinePort,
            TrustedOrganizationInboxRefFactory inboxRefFactory) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.confirmationService = confirmationService;
        this.taskProofPort = taskProofPort;
        this.quarantinePort = quarantinePort;
        this.inboxRefFactory = inboxRefFactory;
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
            case "DEVICE_FAULT_OBSERVED" ->
                    effectKind = observeFault(
                            event,
                            deployment,
                            edge.eventId(),
                            tenantId,
                            organizationId,
                            edge.receivedAt());
            case "DEVICE_FAULT_RECOVERED" ->
                    effectKind = observeFaultRecovery(
                            event,
                            deployment,
                            edge.eventId(),
                            tenantId,
                            organizationId,
                            edge.receivedAt());
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
                        UPDATE dev_deployment_runtime_state
                        SET edge_connection_status = 'ONLINE',
                            mcu_link_status = ?,
                            aggregate_weight_health = ?,
                            local_storage_health = ?,
                            clock_sync_health = ?,
                            edge_boot_id = ?,
                            edge_software_version = ?,
                            mcu_firmware_version = ?,
                            mcu_boot_id = ?,
                            uart_state = ?,
                            uart_protocol_major = ?,
                            uart_protocol_minor = ?,
                            capability_bitmap_hex = ?,
                            local_storage_state = ?,
                            clock_state = ?,
                            pending_reliable_event_count = ?,
                            trusted_runtime_edge_event_id = ?,
                            trusted_runtime_edge_event_type =
                                'DEVICE_RUNTIME_SNAPSHOT',
                            trusted_runtime_sequence = ?,
                            trusted_runtime_received_at = ?,
                            orange_pi_reported_config_version_no = ?,
                            orange_pi_reported_config_content_sha256 = ?,
                            orange_pi_reported_config_mcu_payload_sha256 = ?,
                            last_heartbeat_at = ?,
                            last_device_event_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND (
                              trusted_runtime_sequence IS NULL
                              OR trusted_runtime_sequence < ?
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
                            smoke_state = ?,
                            smoke_sensor_health = ?,
                            runtime_fault_bitmap = ?,
                            safety_status = CASE
                                WHEN safety_projection_sequence IS NULL
                                  OR safety_projection_sequence < ?
                                THEN ?
                                ELSE safety_status
                            END,
                            safety_projection_edge_event_id = CASE
                                WHEN safety_projection_sequence IS NULL
                                  OR safety_projection_sequence < ?
                                THEN ?
                                ELSE safety_projection_edge_event_id
                            END,
                            safety_projection_edge_event_type = CASE
                                WHEN safety_projection_sequence IS NULL
                                  OR safety_projection_sequence < ?
                                THEN 'DEVICE_RUNTIME_SNAPSHOT'
                                ELSE safety_projection_edge_event_type
                            END,
                            safety_projection_sequence = CASE
                                WHEN safety_projection_sequence IS NULL
                                  OR safety_projection_sequence < ?
                                THEN ?
                                ELSE safety_projection_sequence
                            END,
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
                smokeState,
                smokeHealth,
                nonNegativeLong(port, "faultBitmap"),
                sequence,
                portSafety,
                sequence,
                edgeEventId,
                sequence,
                sequence,
                sequence,
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

    private String observeFault(
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
            verifyFaultIdentity(
                    row, deployment.deploymentId(), portId,
                    component, faultCode, impact);
            if ("OPEN".equals(row.status())) {
                requireSingle(jdbc.update("""
                                UPDATE dev_device_fault_event
                                SET last_detected_at = ?,
                                    discovery_count =
                                        discovery_count + 1,
                                    lock_version = lock_version + 1
                                WHERE id = ?
                                """,
                        now,
                        row.id()),
                        "merge repeated trusted device fault");
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
        return effect;
    }

    private String observeFaultRecovery(
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
        List<FaultRow> faults = loadFault(faultUid);
        if (!faults.isEmpty()) {
            FaultRow fault = faults.getFirst();
            verifyFaultIdentity(
                    fault, deployment.deploymentId(), portId,
                    component, faultCode, impact);
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
        return faults.isEmpty() ? "CREATED" : "UPDATED";
    }

    private void applyStoredRecoveryIfPresent(
            FaultRow fault,
            String faultUid,
            LocalDateTime now) {
        List<RecoveryObservation> rows = jdbc.query("""
                        SELECT source_edge_event_id
                        FROM dev_fault_recovery_observation
                        WHERE fault_uid = ?
                        """,
                (rs, ignored) -> new RecoveryObservation(
                        rs.getLong("source_edge_event_id")),
                faultUid);
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

    private static void verifyFaultIdentity(
            FaultRow row,
            long deploymentId,
            Long portId,
            String component,
            String faultCode,
            String impact) {
        if (row.deploymentId() != deploymentId
                || !java.util.Objects.equals(row.portId(), portId)
                || !row.component().equals(component)
                || !row.faultCode().equals(faultCode)
                || !row.impact().equals(impact)) {
            throw new UntrustedInboxSourceException(
                    "fault identity carries conflicting semantics");
        }
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
            String fullnessValue, String sampleBasis) {
        if ("NOT_SAMPLED".equals(sampleBasis)) {
            return "UNKNOWN";
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
            long edgeEventId) {
    }

    private record ConfirmationTarget(
            String taskState,
            String semanticEnvelope) {
    }
}
