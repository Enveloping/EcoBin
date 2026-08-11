package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.application.delivery.ApplyDeliveryCommandObservationService;
import org.enveloping.ecobin.device.api.port.ApplyTrustedPhotoStatusBusinessPort;
import org.enveloping.ecobin.device.api.port.TrustedCleanCommandObservationBusinessPort;
import org.enveloping.ecobin.device.api.port.TrustedEdgeRestartedBusinessPort;
import org.enveloping.ecobin.device.api.port.TrustedPlatformConfirmationReceiptPort;
import org.enveloping.ecobin.device.api.result.PhotoStatusBusinessResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;
import org.enveloping.ecobin.device.api.result.TrustedCleanCommandObservation;
import org.enveloping.ecobin.device.api.result.TrustedEdgeRestartedWork;
import org.enveloping.ecobin.device.api.result.TrustedPhotoStatusFact;
import org.enveloping.ecobin.device.api.result.TrustedPlatformConfirmationReceiptEvent;
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
public class TrustedOrangePiRuntimeFactService
        implements TrustedPlatformConfirmationReceiptPort {

    private static final Set<String> SUPPORTED = Set.of(
            "DEVICE_COMMAND_OBSERVED",
            "DEVICE_RUNTIME_SNAPSHOT",
            "DEVICE_FAULT_OBSERVED",
            "DEVICE_FAULT_RECOVERED",
            "SAFETY_SENSOR_STATE_CHANGED",
            "BASELINE_MEASUREMENT_COMPLETE",
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

    static final String LOAD_BASELINE_LOCK_COORDINATES_SQL = """
            SELECT id AS measurement_id, port_id
            FROM rec_port_baseline_measurement
            WHERE tenant_id = ?
              AND organization_id = ?
              AND asset_id = ?
              AND measurement_uid = ?
            """;

    static final String LOCK_BASELINE_RUNTIME_SQL = """
            SELECT asset_id
            FROM dev_device_runtime_state
            WHERE tenant_id = ?
              AND organization_id = ?
              AND asset_id = ?
            FOR UPDATE
            """;

    static final String LOCK_BASELINE_CAPACITY_SQL = """
            SELECT port_id
            FROM rec_port_capacity_state
            WHERE tenant_id = ?
              AND organization_id = ?
              AND asset_id = ?
              AND port_id = ?
            FOR UPDATE
            """;

    static final String LOCK_BASELINE_MEASUREMENT_SQL = """
            SELECT id
            FROM rec_port_baseline_measurement
            WHERE tenant_id = ?
              AND organization_id = ?
              AND asset_id = ?
              AND port_id = ?
              AND id = ?
            FOR UPDATE
            """;

    static final String LOCK_BASELINE_COMMAND_SQL = """
            SELECT id
            FROM dev_device_command
            WHERE tenant_id = ?
              AND organization_id = ?
              AND asset_id = ?
              AND baseline_measurement_id = ?
              AND command_type = 'MEASURE_EMPTY_BAG_BASELINE'
              AND command_uid = ?
            FOR UPDATE
            """;

    static final String LOAD_BASELINE_TARGET_SQL = """
            SELECT measurement.id AS measurement_id,
                   measurement.status AS measurement_status,
                   measurement.capacity_lock_version_snapshot,
                   measurement.fullness_rule_fingerprint,
                   port.id AS port_id, port.port_no,
                   bag.id AS bag_id, bag.bag_uid,
                   version.id AS config_id,
                   version.version_no,
                   LOWER(HEX(version.content_sha256))
                       AS content_sha256,
                   LOWER(HEX(version.mcu_payload_sha256))
                       AS mcu_payload_sha256,
                   snapshot.id AS snapshot_id,
                   snapshot.calibration_version,
                   capacity.lock_version AS capacity_version,
                   capacity.current_bag_id,
                   capacity.current_detection_id,
                   runtime.applied_config_version_no,
                   LOWER(HEX(runtime.applied_config_content_sha256))
                       AS applied_content_sha256,
                   LOWER(HEX(runtime.applied_mcu_payload_sha256))
                       AS applied_mcu_payload_sha256,
                   command_row.id AS command_id,
                   command_row.physical_state AS command_state,
                   factory_bag.id AS factory_bag_id
            FROM rec_port_baseline_measurement measurement
            JOIN dev_port port
              ON port.tenant_id = measurement.tenant_id
             AND port.organization_id = measurement.organization_id
             AND port.asset_id = measurement.asset_id
             AND port.id = measurement.port_id
            JOIN rec_bag bag
              ON bag.tenant_id = measurement.tenant_id
             AND bag.organization_id = measurement.organization_id
             AND bag.id = measurement.bag_id
            JOIN dev_config_version version
              ON version.tenant_id = measurement.tenant_id
             AND version.organization_id =
                 measurement.organization_id
             AND version.asset_id = measurement.asset_id
             AND version.id = measurement.device_config_version_id
            JOIN dev_port_config_snapshot snapshot
              ON snapshot.tenant_id = measurement.tenant_id
             AND snapshot.organization_id =
                 measurement.organization_id
             AND snapshot.asset_id = measurement.asset_id
             AND snapshot.config_version_id = version.id
             AND snapshot.port_id = port.id
             AND snapshot.id = measurement.port_config_snapshot_id
            JOIN rec_port_capacity_state capacity
              ON capacity.tenant_id = measurement.tenant_id
             AND capacity.organization_id = measurement.organization_id
             AND capacity.asset_id = measurement.asset_id
             AND capacity.port_id = port.id
            JOIN dev_device_runtime_state runtime
              ON runtime.tenant_id = measurement.tenant_id
             AND runtime.organization_id = measurement.organization_id
             AND runtime.asset_id = measurement.asset_id
            JOIN dev_device_command command_row
              ON command_row.tenant_id = measurement.tenant_id
             AND command_row.organization_id = measurement.organization_id
             AND command_row.asset_id = measurement.asset_id
             AND command_row.baseline_measurement_id = measurement.id
             AND command_row.command_type = 'MEASURE_EMPTY_BAG_BASELINE'
            JOIN dev_factory_installed_bag factory_bag
              ON factory_bag.asset_id = measurement.asset_id
             AND factory_bag.port_no = port.port_no
             AND factory_bag.bag_code = bag.bag_code
            WHERE measurement.tenant_id = ?
              AND measurement.organization_id = ?
              AND measurement.asset_id = ?
              AND measurement.measurement_uid = ?
              AND command_row.command_uid = ?
            """;

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final ReliableEdgeConfirmationService confirmationService;
    private final ReliableDeviceTaskProofPort taskProofPort;
    private final TrustedInboxQuarantinePort quarantinePort;
    private final TrustedOrganizationInboxRefFactory inboxRefFactory;
    private final ApplyTrustedPhotoStatusBusinessPort photoStatusBusiness;
    private final ReliablePhotoUploadGrantService photoUploadGrants;
    private final ApplyDeliveryCommandObservationService
            deliveryCommandObservation;
    private final BaselineMeasurementTechnicalAbortService
            baselineTechnicalAborts;
    private final List<TrustedCleanCommandObservationBusinessPort>
            cleanCommandObservationBusinessPorts;
    private final List<TrustedEdgeRestartedBusinessPort>
            edgeRestartedBusinessPorts;

    public TrustedOrangePiRuntimeFactService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            ReliableEdgeConfirmationService confirmationService,
            ReliableDeviceTaskProofPort taskProofPort,
            TrustedInboxQuarantinePort quarantinePort,
            TrustedOrganizationInboxRefFactory inboxRefFactory,
            ApplyTrustedPhotoStatusBusinessPort photoStatusBusiness,
            ReliablePhotoUploadGrantService photoUploadGrants,
            ApplyDeliveryCommandObservationService
                    deliveryCommandObservation,
            BaselineMeasurementTechnicalAbortService
                    baselineTechnicalAborts,
            List<TrustedCleanCommandObservationBusinessPort>
                    cleanCommandObservationBusinessPorts,
            List<TrustedEdgeRestartedBusinessPort>
                    edgeRestartedBusinessPorts) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.confirmationService = confirmationService;
        this.taskProofPort = taskProofPort;
        this.quarantinePort = quarantinePort;
        this.inboxRefFactory = inboxRefFactory;
        this.photoStatusBusiness = photoStatusBusiness;
        this.photoUploadGrants = photoUploadGrants;
        this.deliveryCommandObservation = deliveryCommandObservation;
        this.baselineTechnicalAborts = baselineTechnicalAborts;
        this.cleanCommandObservationBusinessPorts = List.copyOf(
                cleanCommandObservationBusinessPorts);
        this.edgeRestartedBusinessPorts = List.copyOf(
                edgeRestartedBusinessPorts);
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public TrustedDeviceEventApplyResult apply(
            TrustedDeviceInboxEvent inboxEvent) {
        if (!SUPPORTED.contains(inboxEvent.messageKind())
                || inboxEvent.normalizedSchemaVersion()
                != TrustedDeviceInboxEvent.CURRENT_NORMALIZED_SCHEMA_VERSION) {
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

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public void apply(
            TrustedPlatformConfirmationReceiptEvent inboxEvent) {
        if (inboxEvent.normalizedSchemaVersion()
                != TrustedDeviceInboxEvent.CURRENT_NORMALIZED_SCHEMA_VERSION) {
            throw new IllegalArgumentException(
                    "unsupported platform confirmation receipt schema");
        }
        ParsedEvent event = parse(
                "BUSINESS_CONFIRMATION_RECEIPT",
                inboxEvent.normalizedPayload());
        inboxEvent.sourceInbox().use(ignoredInboxId -> {
            AssetTarget asset = loadPlatformAsset(event);
            applyConfirmationReceipt(
                    event,
                    asset,
                    "PLATFORM",
                    null,
                    null);
            return null;
        });
    }

    private TrustedDeviceEventApplyResult applyWithinScope(
            ParsedEvent event,
            long inboxId,
            long tenantId,
            long organizationId) {
        AssetTarget asset = loadAsset(
                event, tenantId, organizationId);
        EdgeInsert edge = insertEdgeEvent(
                event,
                asset,
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
                CommandObservationResult result;
                try {
                    result = applyCommandObservation(
                            event,
                            asset,
                            edge.eventId(),
                            tenantId,
                            organizationId,
                            edge.receivedAt());
                } catch (UntrustedInboxSourceException obsoleteTarget) {
                    return quarantineObsoleteTarget(
                            event,
                            asset,
                            inboxId,
                            tenantId,
                            organizationId,
                            edge.receivedAt(),
                            "device command observation references "
                                    + "an obsolete target");
                }
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
                            asset.assetId(),
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
                        asset,
                        edge.eventId(),
                        tenantId,
                        organizationId,
                        edge.receivedAt());
                return TrustedDeviceEventApplyResult.APPLIED;
            }
            case "DEVICE_FAULT_OBSERVED" -> {
                FaultApplyResult result = observeFault(
                        event,
                        asset,
                        edge.eventId(),
                        tenantId,
                        organizationId,
                        edge.receivedAt());
                if (result.conflict()) {
                    return quarantineFaultConflict(
                            event,
                            asset,
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
                        asset,
                        edge.eventId(),
                        tenantId,
                        organizationId,
                        edge.receivedAt());
                if (result.conflict()) {
                    return quarantineFaultConflict(
                            event,
                            asset,
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
                        asset,
                        edge.eventId(),
                        tenantId,
                        organizationId,
                        edge.receivedAt());
                effectKind = "UPDATED";
            }
            case "BASELINE_MEASUREMENT_COMPLETE" ->
                    effectKind = applyBaselineMeasurementComplete(
                            event,
                            asset,
                            edge.eventId(),
                            tenantId,
                            organizationId,
                            edge.receivedAt());
            case "PHOTO_STATUS_REPORTED" -> {
                PhotoStatusBusinessResult result =
                        photoStatusBusiness.apply(photoStatusFact(
                                event,
                                asset,
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
                            asset.assetId(),
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
                        asset.assetId(),
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
                                asset.assetId(),
                                edge.eventId(),
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
                            asset.assetId(),
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
                        asset.assetId(),
                        event.eventUid(),
                        event.payloadSha256(),
                        result.effectKind(),
                        edge.receivedAt());
                return TrustedDeviceEventApplyResult.APPLIED;
            }
            case "BUSINESS_CONFIRMATION_RECEIPT" -> {
                applyConfirmationReceipt(
                        event,
                        asset,
                        "ORGANIZATION",
                        tenantId,
                        organizationId);
                return TrustedDeviceEventApplyResult.APPLIED;
            }
            default -> throw new IllegalStateException(
                    "trusted event router lost its event type");
        }

        confirmationService.registerApplied(
                tenantId,
                organizationId,
                asset.assetId(),
                event.eventUid(),
                event.payloadSha256(),
                effectKind,
                edge.receivedAt());
        return TrustedDeviceEventApplyResult.APPLIED;
    }

    private TrustedDeviceEventApplyResult quarantineFaultConflict(
            ParsedEvent event,
            AssetTarget asset,
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
                asset.assetId(),
                event.eventUid(),
                event.payloadSha256(),
                result.conflictCode(),
                quarantineUid,
                receivedAt);
        return TrustedDeviceEventApplyResult.QUARANTINED;
    }

    private TrustedDeviceEventApplyResult quarantineObsoleteTarget(
            ParsedEvent event,
            AssetTarget asset,
            long inboxId,
            long tenantId,
            long organizationId,
            LocalDateTime receivedAt,
            String diagnostic) {
        UUID quarantineUid = quarantinePort.quarantine(
                inboxRefFactory.issue(
                        inboxId, tenantId, organizationId),
                "EVENT_TARGET_NOT_AUTHORITATIVE",
                diagnostic);
        confirmationService.registerQuarantined(
                tenantId,
                organizationId,
                asset.assetId(),
                event.eventUid(),
                event.payloadSha256(),
                "EVENT_TARGET_NOT_AUTHORITATIVE",
                quarantineUid,
                receivedAt);
        return TrustedDeviceEventApplyResult.QUARANTINED;
    }

    private AssetTarget loadAsset(
            ParsedEvent event,
            long tenantId,
            long organizationId) {
        List<AssetTarget> assets = jdbc.query("""
                        SELECT id, expected_port_count
                        FROM dev_device_asset
                        WHERE hardware_sn = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new AssetTarget(
                        rs.getLong("id"),
                        rs.getInt("expected_port_count")),
                event.hardwareSn(),
                tenantId,
                organizationId);
        if (assets.size() != 1) {
            throw new UntrustedInboxSourceException(
                    "authenticated Orange Pi asset is not authoritative");
        }
        return assets.getFirst();
    }

    private EdgeInsert insertEdgeEvent(
            ParsedEvent event,
            AssetTarget asset,
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
                               asset_id = ?
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
                asset.assetId(),
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
                            asset.assetId(),
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
                            asset_id, edge_event_sequence,
                            event_type, delivery_class, schema_version,
                            target_type, target_stable_key_sha256,
                            device_occurred_at, clock_quality,
                            backend_received_at, payload_sha256,
                            canonical_sha256, source_inbox_id, created_at
                        ) VALUES (
                            ?, ?, ?,
                            ?, ?,
                            ?, ?, 2,
                            ?, ?,
                            ?, ?,
                            ?, ?,
                            ?, ?, ?
                        )
                        """,
                event.eventUid(),
                tenantId,
                organizationId,
                asset.assetId(),
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

    private String applyBaselineMeasurementComplete(
            ParsedEvent event,
            AssetTarget asset,
            long edgeEventId,
            long tenantId,
            long organizationId,
            LocalDateTime now) {
        JsonNode payload = event.payload();
        String measurementUid = requiredText(payload, "measurementUid");
        String commandUid = event.commandUid();
        if (commandUid == null
                || !measurementUid.equals(event.targetUid())) {
            throw new UntrustedInboxSourceException(
                    "baseline result target is not authoritative");
        }
        List<BaselineLockCoordinates> coordinateRows = jdbc.query(
                LOAD_BASELINE_LOCK_COORDINATES_SQL,
                (rs, ignored) -> new BaselineLockCoordinates(
                        rs.getLong("measurement_id"),
                        rs.getLong("port_id")),
                tenantId,
                organizationId,
                asset.assetId(),
                measurementUid);
        if (coordinateRows.size() != 1) {
            throw new UntrustedInboxSourceException(
                    "baseline result does not resolve its frozen intent");
        }
        BaselineLockCoordinates coordinates = coordinateRows.getFirst();

        requireBaselineLock(
                LOCK_BASELINE_RUNTIME_SQL,
                tenantId,
                organizationId,
                asset.assetId());
        requireBaselineLock(
                LOCK_BASELINE_CAPACITY_SQL,
                tenantId,
                organizationId,
                asset.assetId(),
                coordinates.portId());
        requireBaselineLock(
                LOCK_BASELINE_MEASUREMENT_SQL,
                tenantId,
                organizationId,
                asset.assetId(),
                coordinates.portId(),
                coordinates.measurementId());
        requireBaselineLock(
                LOCK_BASELINE_COMMAND_SQL,
                tenantId,
                organizationId,
                asset.assetId(),
                coordinates.measurementId(),
                commandUid);

        List<BaselineTarget> rows = jdbc.query(
                LOAD_BASELINE_TARGET_SQL,
                (rs, ignored) -> new BaselineTarget(
                        rs.getLong("measurement_id"),
                        rs.getString("measurement_status"),
                        rs.getLong("capacity_lock_version_snapshot"),
                        rs.getBytes("fullness_rule_fingerprint"),
                        rs.getLong("port_id"),
                        rs.getInt("port_no"),
                        rs.getLong("bag_id"),
                        rs.getString("bag_uid"),
                        rs.getLong("config_id"),
                        rs.getLong("version_no"),
                        rs.getString("content_sha256"),
                        rs.getString("mcu_payload_sha256"),
                        rs.getLong("snapshot_id"),
                        rs.getLong("calibration_version"),
                        rs.getLong("capacity_version"),
                        nullableDatabaseLong(rs, "current_bag_id"),
                        nullableDatabaseLong(rs, "current_detection_id"),
                        nullableDatabaseLong(
                                rs, "applied_config_version_no"),
                        rs.getString("applied_content_sha256"),
                        rs.getString("applied_mcu_payload_sha256"),
                        rs.getLong("command_id"),
                        rs.getString("command_state"),
                        rs.getLong("factory_bag_id")),
                tenantId,
                organizationId,
                asset.assetId(),
                measurementUid,
                commandUid);
        if (rows.size() != 1) {
            throw new UntrustedInboxSourceException(
                    "baseline result does not resolve its frozen intent");
        }
        BaselineTarget target = rows.getFirst();
        boolean technicallyAborted = "TECHNICAL_ABORTED".equals(
                target.measurementStatus());
        if (!canApplyBaselineResult(
                target.measurementStatus(), target.commandState())) {
            throw new UntrustedInboxSourceException(
                    "baseline intent is already terminal");
        }

        JsonNode frozen = requiredObject(payload, "frozenConfig");
        BaselineMeasurementFact measurement = baselineMeasurement(
                requiredObject(payload, "totalWeightMeasurement"));
        if (positiveInt(payload, "portNo") != target.portNo()
                || !target.bagUid().equals(requiredText(payload, "bagUid"))
                || !requiredBoolean(payload, "emptyBagConfirmed")
                || positiveLong(frozen, "version") != target.versionNo()
                || !target.contentSha256().equals(
                        requiredText(frozen, "contentSha256"))
                || !target.mcuPayloadSha256().equals(
                        requiredText(frozen, "mcuPayloadSha256"))
                || measurement.calibrationVersion()
                        != target.calibrationVersion()) {
            throw new UntrustedInboxSourceException(
                    "baseline result differs from its frozen intent");
        }

        NormalizedBaselineMeasurement normalized =
                normalizeBaselineMeasurement(measurement);
        long physicalResultId = insertBaselinePhysicalResult(
                event,
                target,
                normalized,
                edgeEventId,
                tenantId,
                organizationId,
                asset.assetId(),
                now);
        boolean stale = technicallyAborted || target.capacityVersion()
                != target.capacityVersionSnapshot()
                || !Long.valueOf(target.bagId()).equals(
                        target.currentBagId())
                || target.currentDetectionId() != null
                || !Long.valueOf(target.versionNo()).equals(
                        target.appliedVersionNo())
                || !target.contentSha256().equals(
                        target.appliedContentSha256())
                || !target.mcuPayloadSha256().equals(
                        target.appliedMcuPayloadSha256());
        boolean success = !stale
                && "STABLE".equals(normalized.status())
                && normalized.weightGrams() != null
                && normalized.weightGrams() >= 0;
        if (technicallyAborted) {
            applyLateTechnicallyAbortedBaseline(
                    target,
                    physicalResultId,
                    now);
        } else if (success) {
            applySuccessfulBaseline(
                    target,
                    physicalResultId,
                    normalized.weightGrams(),
                    tenantId,
                    organizationId,
                    asset.assetId(),
                    now);
        } else {
            String failureCode = stale
                    ? "BASELINE_FACT_STALE"
                    : "STABLE".equals(normalized.status())
                    ? "NEGATIVE_EMPTY_BAG_WEIGHT"
                    : normalized.faultCode();
            applyFailedBaseline(
                    target,
                    physicalResultId,
                    failureCode,
                    stale,
                    tenantId,
                    organizationId,
                    asset.assetId(),
                    now);
        }
        if (!technicallyAborted) {
            completeBaselineCommand(
                    target,
                    tenantId,
                    organizationId,
                    asset.assetId(),
                    now);
        }
        taskProofPort.completeDispatchFromTrustedCommandObservation(
                UUID.fromString(commandUid));
        touchLastDeviceEvent(
                asset.assetId(), tenantId, organizationId, now);
        // The confirmation contract describes the generic database effect;
        // the detailed success or retry state remains in the baseline rows.
        return "UPDATED";
    }

    private void applyLateTechnicallyAbortedBaseline(
            BaselineTarget target,
            long physicalResultId,
            LocalDateTime now) {
        requireSingle(jdbc.update("""
                        UPDATE rec_port_baseline_measurement
                        SET status = 'STALE_IGNORED',
                            physical_result_id = ?,
                            stable_total_weight_g = NULL,
                            result_baseline_id = NULL,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND status = 'TECHNICAL_ABORTED'
                        """,
                physicalResultId,
                now,
                target.measurementId()),
                "store late technically aborted baseline result");
    }

    private void requireBaselineLock(String sql, Object... arguments) {
        List<Long> lockedRows = jdbc.query(
                sql,
                (rs, ignored) -> rs.getLong(1),
                arguments);
        if (lockedRows.size() != 1) {
            throw new UntrustedInboxSourceException(
                    "baseline result does not resolve its frozen intent");
        }
    }

    private long insertBaselinePhysicalResult(
            ParsedEvent event,
            BaselineTarget target,
            NormalizedBaselineMeasurement measurement,
            long edgeEventId,
            long tenantId,
            long organizationId,
            long assetId,
            LocalDateTime now) {
        requireSingle(jdbc.update("""
                        INSERT INTO dev_physical_result (
                            tenant_id, organization_id, asset_id,
                            port_id, edge_event_id, edge_event_type,
                            command_id, command_type,
                            reported_config_version_no,
                            reported_config_content_sha256,
                            reported_config_mcu_payload_sha256,
                            result_type, delivery_session_id,
                            clean_operation_id, fullness_sample_id,
                            baseline_measurement_id,
                            baseline_measurement_uid,
                            baseline_measurement_status,
                            baseline_total_weight_g,
                            baseline_last_observed_weight_g,
                            baseline_measurement_elapsed_ms,
                            baseline_sample_count,
                            baseline_calibration_version,
                            baseline_sensor_health,
                            baseline_fault_code,
                            baseline_mcu_boot_id,
                            baseline_mcu_event_sequence,
                            empty_bag_confirmed,
                            uart_protocol_major, uart_protocol_minor,
                            created_at
                        ) VALUES (
                            ?, ?, ?, ?, ?,
                            'BASELINE_MEASUREMENT_COMPLETE',
                            ?, 'MEASURE_EMPTY_BAG_BASELINE',
                            ?, ?, ?,
                            'BASELINE_MEASUREMENT',
                            NULL, NULL, NULL, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1,
                            NULL, NULL, ?
                        )
                        """,
                tenantId,
                organizationId,
                assetId,
                target.portId(),
                edgeEventId,
                target.commandId(),
                target.versionNo(),
                HexFormat.of().parseHex(target.contentSha256()),
                HexFormat.of().parseHex(target.mcuPayloadSha256()),
                target.measurementId(),
                measurement.measurementUid(),
                measurement.status(),
                "STABLE".equals(measurement.status())
                        ? measurement.weightGrams() : null,
                "STABLE".equals(measurement.status())
                        ? null : measurement.weightGrams(),
                measurement.elapsedMs(),
                measurement.sampleCount(),
                measurement.calibrationVersion(),
                measurement.sensorHealth(),
                measurement.faultCode(),
                measurement.mcuBootId(),
                measurement.mcuEventSequence(),
                now),
                "insert baseline physical result");
        Long resultId = jdbc.queryForObject("""
                        SELECT id
                        FROM dev_physical_result
                        WHERE edge_event_id = ?
                        """,
                Long.class,
                edgeEventId);
        if (resultId == null) {
            throw new IllegalStateException(
                    "baseline physical result id is missing");
        }
        return resultId;
    }

    private void applySuccessfulBaseline(
            BaselineTarget target,
            long physicalResultId,
            long weightGrams,
            long tenantId,
            long organizationId,
            long assetId,
            LocalDateTime now) {
        Long latestVersion = jdbc.queryForObject("""
                        SELECT COALESCE(MAX(version_no), 0)
                        FROM rec_port_weight_baseline
                        WHERE port_id = ?
                        """,
                Long.class,
                target.portId());
        long baselineVersion = (latestVersion == null ? 0 : latestVersion) + 1;
        requireSingle(jdbc.update("""
                        INSERT INTO rec_port_weight_baseline (
                            tenant_id, organization_id,
                            port_id, bag_id, version_no,
                            source_type, source_bag_event_id,
                            source_physical_result_id,
                            source_clean_record_id,
                            source_measurement_id,
                            baseline_weight_g,
                            established_at, created_at
                        ) VALUES (
                            ?, ?, ?, ?, ?,
                            'AUTOMATIC_INITIAL', NULL, ?, NULL, ?,
                            ?, ?, ?
                        )
                        """,
                tenantId,
                organizationId,
                target.portId(),
                target.bagId(),
                baselineVersion,
                physicalResultId,
                target.measurementId(),
                weightGrams,
                now,
                now),
                "insert automatic initial baseline");
        Long baselineId = jdbc.queryForObject("""
                        SELECT id
                        FROM rec_port_weight_baseline
                        WHERE source_measurement_id = ?
                        """,
                Long.class,
                target.measurementId());
        if (baselineId == null) {
            throw new IllegalStateException(
                    "automatic initial baseline id is missing");
        }
        requireSingle(jdbc.update("""
                        UPDATE rec_port_baseline_measurement
                        SET status = 'COMPLETED',
                            physical_result_id = ?,
                            stable_total_weight_g = ?,
                            fault_code = NULL,
                            result_baseline_id = ?,
                            completed_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ? AND status = 'PENDING'
                        """,
                physicalResultId,
                weightGrams,
                baselineId,
                now,
                now,
                target.measurementId()),
                "complete automatic baseline measurement");
        requireSingle(jdbc.update("""
                        UPDATE rec_port_capacity_state
                        SET baseline_state = 'VALID',
                            current_baseline_id = ?,
                            current_baseline_weight_g = ?,
                            latest_stable_total_weight_g = ?,
                            raw_net_weight_g = 0,
                            displayed_fullness_percent = 0,
                            detection_gate = 'READY',
                            current_detection_id = NULL,
                            current_rule_fingerprint = ?,
                            confirmed_fullness_state = 'NOT_FULL',
                            last_detection_id = NULL,
                            current_fullness_event_id = NULL,
                            current_bag_id = ?,
                            current_fullness_state_change_id = NULL,
                            last_fullness_edge_event_id = NULL,
                            last_fullness_edge_event_sequence = NULL,
                            last_fullness_reported_at = NULL,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND port_id = ?
                          AND lock_version = ?
                          AND current_bag_id = ?
                          AND current_detection_id IS NULL
                        """,
                baselineId,
                weightGrams,
                weightGrams,
                target.ruleFingerprint(),
                target.bagId(),
                now,
                tenantId,
                organizationId,
                assetId,
                target.portId(),
                target.capacityVersionSnapshot(),
                target.bagId()),
                "project automatic baseline capacity");
        requireSingle(jdbc.update("""
                        UPDATE dev_factory_installed_bag
                        SET tare_status = 'READY',
                            last_failure_code = NULL,
                            updated_at = ?
                        WHERE id = ?
                        """,
                now,
                target.factoryBagId()),
                "mark factory bag tare ready");
    }

    private void applyFailedBaseline(
            BaselineTarget target,
            long physicalResultId,
            String failureCode,
            boolean stale,
            long tenantId,
            long organizationId,
            long assetId,
            LocalDateTime now) {
        String terminalStatus = stale ? "STALE_IGNORED" : "FAILED";
        requireSingle(jdbc.update("""
                        UPDATE rec_port_baseline_measurement
                        SET status = ?,
                            physical_result_id = ?,
                            stable_total_weight_g = NULL,
                            fault_code = ?,
                            result_baseline_id = NULL,
                            completed_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ? AND status = 'PENDING'
                        """,
                terminalStatus,
                physicalResultId,
                failureCode,
                now,
                now,
                target.measurementId()),
                "fail automatic baseline measurement");
        jdbc.update("""
                        UPDATE rec_port_capacity_state
                        SET detection_gate = 'FAILED',
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND port_id = ?
                          AND current_baseline_id IS NULL
                        """,
                now,
                tenantId,
                organizationId,
                assetId,
                target.portId());
        requireSingle(jdbc.update("""
                        UPDATE dev_factory_installed_bag
                        SET tare_status = 'FAILED',
                            last_failure_code = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                failureCode,
                now,
                target.factoryBagId()),
                "mark factory bag tare failed");
    }

    private void completeBaselineCommand(
            BaselineTarget target,
            long tenantId,
            long organizationId,
            long assetId,
            LocalDateTime now) {
        requireSingle(jdbc.update("""
                        UPDATE dev_device_command
                        SET physical_state = 'PHYSICAL_SUCCEEDED',
                            edge_accepted_at = COALESCE(edge_accepted_at, ?),
                            physical_started_at =
                                COALESCE(physical_started_at, ?),
                            physical_ended_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND physical_state IN (
                              'QUEUED', 'EDGE_ACCEPTED', 'PHYSICAL_STARTED'
                          )
                        """,
                now,
                now,
                now,
                now,
                target.commandId(),
                tenantId,
                organizationId,
                assetId),
                "complete baseline device command");
    }

    private static BaselineMeasurementFact baselineMeasurement(JsonNode node) {
        return new BaselineMeasurementFact(
                requiredText(node, "measurementUid"),
                requiredText(node, "status"),
                requiredBoolean(node, "weightValueAvailable"),
                nullableLong(node, "reportedWeightGrams"),
                requiredText(node, "weightValueKind"),
                nonNegativeLong(node, "measurementElapsedMs"),
                nonNegativeLong(node, "sampleCount"),
                nonNegativeLong(node, "calibrationVersion"),
                requiredText(node, "sensorHealth"),
                nullableText(node, "faultCode"),
                positiveLong(node, "mcuBootId"),
                positiveLong(node, "mcuEventSequence"));
    }

    private static NormalizedBaselineMeasurement normalizeBaselineMeasurement(
            BaselineMeasurementFact fact) {
        boolean available = fact.weightValueAvailable()
                && fact.reportedWeightGrams() != null;
        if (available != !"NONE".equals(fact.weightValueKind())) {
            throw new UntrustedInboxSourceException(
                    "baseline measurement value presence differs");
        }
        if ("STABLE".equals(fact.status())) {
            if (!available
                    || fact.sampleCount() < 1
                    || !"OK".equals(fact.sensorHealth())
                    || fact.faultCode() != null) {
                throw new UntrustedInboxSourceException(
                        "stable baseline measurement quality differs");
            }
            return new NormalizedBaselineMeasurement(
                    fact.measurementUid(),
                    "STABLE",
                    fact.reportedWeightGrams(),
                    fact.elapsedMs(),
                    fact.sampleCount(),
                    fact.calibrationVersion(),
                    "OK",
                    null,
                    fact.mcuBootId(),
                    fact.mcuEventSequence());
        }
        String status;
        String health;
        String fault;
        switch (fact.status()) {
            case "UNSTABLE" -> {
                status = "UNSTABLE";
                health = "OK";
                fault = "WEIGHT_UNSTABLE";
            }
            case "TIMEOUT" -> {
                status = "TIMEOUT";
                health = "TIMEOUT";
                fault = "WEIGHT_TIMEOUT";
            }
            case "OVERLOAD" -> {
                status = "OVERLOAD";
                health = "OK";
                fault = "WEIGHT_OVERLOAD";
            }
            case "SENSOR_FAULT", "PROTOCOL_ERROR", "CONFIG_ERROR",
                 "DISCONNECTED" -> {
                status = "SENSOR_FAULT";
                health = "DISCONNECTED".equals(fact.sensorHealth())
                        ? "DISCONNECTED" : "SENSOR_FAULT";
                fault = "WEIGHT_SENSOR";
            }
            default -> throw new UntrustedInboxSourceException(
                    "baseline measurement status is unsupported");
        }
        return new NormalizedBaselineMeasurement(
                fact.measurementUid(),
                status,
                available ? fact.reportedWeightGrams() : null,
                fact.elapsedMs(),
                fact.sampleCount(),
                fact.calibrationVersion(),
                health,
                fault,
                fact.mcuBootId(),
                fact.mcuEventSequence());
    }

    private void applyRuntimeSnapshot(
            ParsedEvent event,
            AssetTarget asset,
            long edgeEventId,
            long tenantId,
            long organizationId,
            LocalDateTime now) {
        JsonNode payload = event.payload();
        JsonNode ports = requiredArray(payload, "ports");
        Map<Integer, Long> portIds = loadPortIds(
                asset.assetId(),
                tenantId,
                organizationId);
        if (ports.size() != asset.portCount()
                || portIds.size() != asset.portCount()) {
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
                        UPDATE dev_device_runtime_state runtime
                        LEFT JOIN dev_device_transport_state transport
                          ON transport.asset_id = runtime.asset_id
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
                          AND runtime.asset_id = ?
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
                asset.assetId(),
                event.sequence());
        if (updated == 0) {
            touchLastDeviceEvent(
                    asset.assetId(),
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
                    asset.assetId(),
                    edgeEventId,
                    event.sequence(),
                    tenantId,
                    organizationId,
                    now);
        }
        refreshSafetyProjection(
                asset.assetId(),
                tenantId,
                organizationId,
                now);
    }

    private CommandObservationResult applyCommandObservation(
            ParsedEvent event,
            AssetTarget asset,
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
                            clean_operation_id, fullness_detection_id,
                            baseline_measurement_id, physical_state
                        FROM dev_device_command
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND command_uid = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new CommandRow(
                        rs.getLong("id"),
                        rs.getString("command_type"),
                        nullableDatabaseLong(
                                rs, "delivery_session_id"),
                        nullableDatabaseLong(
                                rs, "clean_operation_id"),
                        nullableDatabaseLong(
                                rs, "fullness_detection_id"),
                        nullableDatabaseLong(
                                rs, "baseline_measurement_id"),
                        rs.getString("physical_state")),
                tenantId,
                organizationId,
                asset.assetId(),
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
                            tenant_id, organization_id, asset_id,
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
                asset.assetId(),
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
            case "FAILED" -> "EDGE_RESTARTED".equals(errorCode)
                    ? "EDGE_RESTARTED"
                    : "PHYSICAL_FAILED";
            default -> throw new IllegalArgumentException(
                    "device command stage is unsupported");
        };
        boolean shouldAdvance = shouldAdvanceCommand(
                command.physicalState(), desiredState);
        if (shouldAdvance) {
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
                                            'PHYSICAL_FAILED',
                                            'EDGE_RESTARTED'
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
                              AND asset_id = ?
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
                    asset.assetId()),
                    "advance observed device command");
        }
        projectDeliveryCommandObservation(
                command,
                stage,
                errorCode,
                event.occurredAt(),
                now,
                tenantId,
                organizationId,
                asset.assetId());
        projectCleanCommandObservation(
                command,
                stage,
                errorCode,
                event.occurredAt(),
                now,
                tenantId,
                organizationId,
                asset.assetId());
        projectBaselineCommandObservation(
                event.commandUid(),
                command,
                shouldAdvance,
                stage,
                errorCode,
                now);
        if ("FAILED".equals(stage)
                && "EDGE_RESTARTED".equals(errorCode)) {
            abortRestartedWork(
                    command,
                    tenantId,
                    organizationId,
                    asset.assetId(),
                    now);
        }
        return new CommandObservationResult(
                shouldAdvance ? "UPDATED" : "NO_ACTION_REQUIRED",
                false);
    }

    private void projectDeliveryCommandObservation(
            CommandRow command,
            String stage,
            String errorCode,
            LocalDateTime occurredAt,
            LocalDateTime receivedAt,
            long tenantId,
            long organizationId,
            long assetId) {
        if (command.deliverySessionId() == null) {
            return;
        }
        deliveryCommandObservation.apply(
                tenantId,
                organizationId,
                assetId,
                command.deliverySessionId(),
                command.commandType(),
                stage,
                errorCode,
                occurredAt,
                receivedAt);
    }

    private void projectCleanCommandObservation(
            CommandRow command,
            String stage,
            String errorCode,
            LocalDateTime occurredAt,
            LocalDateTime receivedAt,
            long tenantId,
            long organizationId,
            long assetId) {
        if (command.cleanOperationId() == null) {
            return;
        }
        TrustedCleanCommandObservation observation =
                new TrustedCleanCommandObservation(
                        tenantId,
                        organizationId,
                        assetId,
                        command.cleanOperationId(),
                        command.commandType(),
                        stage,
                        errorCode,
                        occurredAt,
                        receivedAt);
        cleanCommandObservationBusinessPorts.forEach(
                port -> port.applyCleanCommandObservation(observation));
    }

    private void projectBaselineCommandObservation(
            String commandUid,
            CommandRow command,
            boolean commandAdvanced,
            String stage,
            String errorCode,
            LocalDateTime receivedAt) {
        if (!shouldTechnicallyAbortBaselineCommand(
                command.baselineMeasurementId() != null,
                commandAdvanced,
                stage,
                errorCode)) {
            return;
        }
        baselineTechnicalAborts.abortByCommand(
                UUID.fromString(commandUid), errorCode, receivedAt);
    }

    static boolean shouldTechnicallyAbortBaselineCommand(
            boolean targetsBaseline,
            boolean commandAdvanced,
            String stage,
            String errorCode) {
        return targetsBaseline
                && commandAdvanced
                && Set.of("REJECTED", "PRE_START_FAILED", "FAILED")
                .contains(stage)
                && !("FAILED".equals(stage)
                && "EDGE_RESTARTED".equals(errorCode));
    }

    static boolean canApplyBaselineResult(
            String measurementStatus,
            String commandState) {
        if ("TECHNICAL_ABORTED".equals(measurementStatus)) {
            return true;
        }
        return "PENDING".equals(measurementStatus)
                && !Set.of(
                        "PHYSICAL_SUCCEEDED",
                        "PHYSICAL_FAILED",
                        "PRE_START_FAILED",
                        "EDGE_RESTARTED")
                .contains(commandState);
    }

    private void abortRestartedWork(
            CommandRow command,
            long tenantId,
            long organizationId,
            long assetId,
            LocalDateTime now) {
        if (command.deliverySessionId() != null) {
            int ended = jdbc.update("""
                            UPDATE dev_delivery_session
                            SET status = 'DEVICE_ABORTED',
                                ended_at = ?,
                                end_reason = 'EDGE_RESTARTED',
                                lock_version = lock_version + 1,
                                updated_at = ?
                            WHERE id = ?
                              AND tenant_id = ?
                              AND organization_id = ?
                              AND asset_id = ?
                              AND status IN (
                                  'PREPARED',
                                  'AUTHORIZATION_QUEUED',
                                  'IN_PROGRESS',
                                  'RESULT_PENDING_RECOVERY'
                              )
                            """,
                    now,
                    now,
                    command.deliverySessionId(),
                    tenantId,
                    organizationId,
                    assetId);
            if (ended == 1) {
                jdbc.update("""
                                DELETE FROM dev_device_occupancy
                                WHERE tenant_id = ?
                                  AND organization_id = ?
                                  AND asset_id = ?
                                  AND occupancy_kind = 'DELIVERY'
                                  AND delivery_session_id = ?
                                """,
                        tenantId,
                        organizationId,
                        assetId,
                        command.deliverySessionId());
            }
        }
        TrustedEdgeRestartedWork work =
                new TrustedEdgeRestartedWork(
                        tenantId,
                        organizationId,
                        assetId,
                        command.commandType(),
                        command.cleanOperationId(),
                        command.fullnessDetectionId(),
                        command.baselineMeasurementId(),
                        now);
        edgeRestartedBusinessPorts.forEach(
                port -> port.abortRestartedWork(work));
    }

    static boolean shouldAdvanceCommand(
            String current,
            String desired) {
        if (Set.of(
                "PHYSICAL_SUCCEEDED",
                "PHYSICAL_FAILED",
                "PRE_START_FAILED",
                "EDGE_RESTARTED").contains(current)) {
            return false;
        }
        return switch (current) {
            case "CREATED", "QUEUED" -> Set.of(
                    "EDGE_ACCEPTED", "PHYSICAL_STARTED",
                    "PRE_START_FAILED", "PHYSICAL_FAILED",
                    "EDGE_RESTARTED").contains(desired);
            case "EDGE_ACCEPTED" -> Set.of(
                    "PHYSICAL_STARTED", "PRE_START_FAILED",
                    "PHYSICAL_FAILED", "EDGE_RESTARTED")
                    .contains(desired);
            case "PHYSICAL_STARTED" -> Set.of(
                    "PHYSICAL_FAILED", "EDGE_RESTARTED")
                    .contains(desired);
            default -> throw new IllegalStateException(
                    "device command has an unsupported state");
        };
    }

    private void mergePortRuntime(
            JsonNode port,
            long portId,
            long assetId,
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
                          AND asset_id = ?
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
                assetId,
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
                assetId,
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
            long assetId,
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
                          AND asset_id = ?
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
                assetId,
                portId,
                sequence);
    }

    private FaultApplyResult observeFault(
            ParsedEvent event,
            AssetTarget asset,
            long edgeEventId,
            long tenantId,
            long organizationId,
            LocalDateTime now) {
        JsonNode payload = event.payload();
        Long portId = resolvePortId(
                payload,
                asset.assetId(),
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
                        asset.assetId(),
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
                                asset_id, port_id, component_type,
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
                    asset.assetId(),
                    portId,
                    component,
                    faultCode,
                    faultKey(
                            tenantId,
                            asset.assetId(),
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
                    row, asset.assetId(), portId,
                    component, faultCode)) {
                return FaultApplyResult.conflict(
                        "FAULT_IDENTITY_CONFLICT",
                        "fault uid carries another asset, port, "
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
                asset.assetId(),
                tenantId,
                organizationId,
                now);
        return FaultApplyResult.applied(effect);
    }

    private FaultApplyResult observeFaultRecovery(
            ParsedEvent event,
            AssetTarget asset,
            long edgeEventId,
            long tenantId,
            long organizationId,
            LocalDateTime now) {
        JsonNode payload = event.payload();
        Long portId = resolvePortId(
                payload,
                asset.assetId(),
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
                    fault, asset.assetId(), portId,
                    component, faultCode)) {
                return FaultApplyResult.conflict(
                        "FAULT_RECOVERY_IDENTITY_CONFLICT",
                        "fault recovery does not identify the same "
                                + "asset, port, component and code");
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
                            asset_id, port_id, component_type,
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
                asset.assetId(),
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
                asset.assetId(),
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
            AssetTarget asset,
            long edgeEventId,
            long tenantId,
            long organizationId,
            LocalDateTime now) {
        JsonNode payload = event.payload();
        Long portId = resolvePortId(
                payload,
                asset.assetId(),
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
                              AND asset_id = ?
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
                    asset.assetId(),
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
                              AND asset_id = ?
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
                    asset.assetId(),
                    portId,
                    event.sequence());
        }
        jdbc.update("""
                        UPDATE dev_device_runtime_state
                        SET safety_projection_edge_event_id = ?,
                            safety_projection_edge_event_type =
                                'SAFETY_SENSOR_STATE_CHANGED',
                            safety_projection_sequence = ?,
                            last_device_event_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
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
                asset.assetId(),
                event.sequence());
        refreshSafetyProjection(
                asset.assetId(),
                tenantId,
                organizationId,
                now);
    }

    private void applyConfirmationReceipt(
            ParsedEvent event,
            AssetTarget asset,
            String scopeKind,
            Long tenantId,
            Long organizationId) {
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
                          AND task.source_device_asset_id = ?
                          AND task.source_device_command_id IS NULL
                          AND task.scope_kind = ?
                          AND task.tenant_id <=> ?
                          AND task.organization_id <=> ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new ConfirmationTarget(
                        rs.getString("state"),
                        rs.getString("semantic_payload")),
                confirmationUid,
                asset.assetId(),
                scopeKind,
                tenantId,
                organizationId);
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

    private AssetTarget loadPlatformAsset(ParsedEvent event) {
        List<AssetTarget> assets = jdbc.query("""
                        SELECT id, expected_port_count
                        FROM dev_device_asset
                        WHERE hardware_sn = ?
                        """,
                (rs, ignored) -> new AssetTarget(
                        rs.getLong("id"),
                        rs.getInt("expected_port_count")),
                event.hardwareSn());
        if (assets.size() != 1) {
            throw new UntrustedInboxSourceException(
                    "platform confirmation device is not registered");
        }
        return assets.getFirst();
    }

    private void refreshSafetyProjection(
            long assetId,
            long tenantId,
            long organizationId,
            LocalDateTime now) {
        Integer safetyFaults = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_device_fault_event
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND status = 'OPEN'
                          AND impact_level = 'SAFETY_BLOCKING'
                        """,
                Integer.class,
                tenantId,
                organizationId,
                assetId);
        Integer businessFaults = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_device_fault_event
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND status = 'OPEN'
                          AND impact_level = 'BUSINESS_BLOCKING'
                          AND fault_code <> 'INITIAL_COMMISSIONING'
                        """,
                Integer.class,
                tenantId,
                organizationId,
                assetId);
        Integer safetyPorts = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_port_runtime_state
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND safety_status = 'SAFETY_BLOCKED'
                        """,
                Integer.class,
                tenantId,
                organizationId,
                assetId);
        Integer blockedPorts = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_port_runtime_state
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND safety_status = 'OPERATION_BLOCKED'
                        """,
                Integer.class,
                tenantId,
                organizationId,
                assetId);
        String status = positive(safetyFaults)
                || positive(safetyPorts)
                ? "SAFETY_BLOCKED"
                : positive(businessFaults)
                || positive(blockedPorts)
                ? "OPERATION_BLOCKED"
                : "SAFE";
        requireSingle(jdbc.update("""
                        UPDATE dev_device_runtime_state
                        SET safety_status = ?,
                            last_device_event_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                        """,
                status,
                now,
                now,
                tenantId,
                organizationId,
                assetId),
                "refresh Orange Pi safety projection");
    }

    private Map<Integer, Long> loadPortIds(
            long assetId,
            long tenantId,
            long organizationId) {
        Map<Integer, Long> result = new HashMap<>();
        jdbc.query("""
                        SELECT id, port_no
                        FROM dev_port
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                        """,
                rs -> {
                    result.put(
                            rs.getInt("port_no"),
                            rs.getLong("id"));
                },
                tenantId,
                organizationId,
                assetId);
        return result;
    }

    private Long resolvePortId(
            JsonNode payload,
            long assetId,
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
                          AND asset_id = ?
                          AND port_no = ?
                        """,
                (rs, ignored) -> rs.getLong("id"),
                tenantId,
                organizationId,
                assetId,
                portNo);
        if (rows.size() != 1) {
            throw new UntrustedInboxSourceException(
                    "event port is not part of the asset");
        }
        return rows.getFirst();
    }

    private List<FaultRow> loadFault(String faultUid) {
        return jdbc.query("""
                        SELECT
                            id, asset_id, port_id,
                            component_type, fault_code,
                            impact_level, status
                        FROM dev_device_fault_event
                        WHERE fault_uid = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new FaultRow(
                        rs.getLong("id"),
                        rs.getLong("asset_id"),
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
                            source_edge_event_id, asset_id,
                            port_id, component_type, fault_code,
                            impact_level
                        FROM dev_fault_recovery_observation
                        WHERE fault_uid = ?
                        """,
                (rs, ignored) -> new RecoveryObservation(
                        rs.getLong("source_edge_event_id"),
                        rs.getLong("asset_id"),
                        nullableDatabaseLong(rs, "port_id"),
                        rs.getString("component_type"),
                        rs.getString("fault_code"),
                        rs.getString("impact_level")),
                faultUid);
    }

    private static boolean sameFaultIdentity(
            FaultRow row,
            long assetId,
            Long portId,
            String component,
            String faultCode) {
        return row.assetId() == assetId
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
                || event.path("schemaVersion").asInt() != 2) {
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
                        : "BASELINE_MEASUREMENT_COMPLETE".equals(eventType)
                        ? "BASELINE_MEASUREMENT"
                        : Set.of(
                                "PHOTO_STATUS_REPORTED",
                                "PHOTO_UPLOAD_GRANT_REQUESTED")
                        .contains(eventType)
                        ? requiredText(
                                requiredObject(event, "payload"),
                                "workType")
                        : "DEVICE_ASSET";
        if (!expectedDelivery.equals(delivery)
                || !expectedTarget.equals(targetType)) {
            throw new IllegalArgumentException(
                    "trusted Orange Pi envelope class differs");
        }
        String trustedDeviceName = requiredText(source, "deviceName");
        if ("DEVICE_ASSET".equals(targetType)
                && !trustedDeviceName.equals(requiredText(target, "uid"))) {
            throw new IllegalArgumentException(
                    "asset target differs from trusted OneNet device");
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
        if ("BASELINE_MEASUREMENT_COMPLETE".equals(eventType)) {
            JsonNode payload = requiredObject(event, "payload");
            if (!requiredText(payload, "measurementUid").equals(
                    requiredText(target, "uid"))
                    || nullableText(event, "commandUid") == null) {
                throw new IllegalArgumentException(
                        "baseline target differs from payload");
            }
        }
        return new ParsedEvent(
                trustedDeviceName,
                requiredText(event, "eventUid"),
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
            AssetTarget asset,
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
                asset.assetId(),
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
            long assetId,
            Long portId,
            String component,
            String faultCode) {
        return sha256(
                tenantId + "\0"
                        + assetId + "\0"
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
            long assetId,
            long tenantId,
            long organizationId,
            LocalDateTime now) {
        requireSingle(jdbc.update("""
                        UPDATE dev_device_runtime_state
                        SET last_device_event_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                        """,
                now,
                now,
                tenantId,
                organizationId,
                assetId),
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

    private record AssetTarget(
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
            long assetId,
            Long portId,
            String component,
            String faultCode,
            String impact,
            String status) {
    }

    private record RecoveryObservation(
            long edgeEventId,
            long assetId,
            Long portId,
            String component,
            String faultCode,
            String impact) {

        private boolean matches(
                long expectedAssetId,
                Long expectedPortId,
                String expectedComponent,
                String expectedFaultCode) {
            return assetId == expectedAssetId
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
            Long cleanOperationId,
            Long fullnessDetectionId,
            Long baselineMeasurementId,
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

    private record BaselineLockCoordinates(
            long measurementId,
            long portId) {
    }

    private record BaselineTarget(
            long measurementId,
            String measurementStatus,
            long capacityVersionSnapshot,
            byte[] ruleFingerprint,
            long portId,
            int portNo,
            long bagId,
            String bagUid,
            long configurationId,
            long versionNo,
            String contentSha256,
            String mcuPayloadSha256,
            long snapshotId,
            long calibrationVersion,
            long capacityVersion,
            Long currentBagId,
            Long currentDetectionId,
            Long appliedVersionNo,
            String appliedContentSha256,
            String appliedMcuPayloadSha256,
            long commandId,
            String commandState,
            long factoryBagId) {
    }

    private record BaselineMeasurementFact(
            String measurementUid,
            String status,
            boolean weightValueAvailable,
            Long reportedWeightGrams,
            String weightValueKind,
            long elapsedMs,
            long sampleCount,
            long calibrationVersion,
            String sensorHealth,
            String faultCode,
            long mcuBootId,
            long mcuEventSequence) {
    }

    private record NormalizedBaselineMeasurement(
            String measurementUid,
            String status,
            Long weightGrams,
            long elapsedMs,
            long sampleCount,
            long calibrationVersion,
            String sensorHealth,
            String faultCode,
            long mcuBootId,
            long mcuEventSequence) {
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
