package org.enveloping.ecobin.recycling.application.clean;

import org.enveloping.ecobin.device.api.command.ScheduleFullnessSampleCommand;
import org.enveloping.ecobin.device.api.port.ReliableEdgeConfirmationPort;
import org.enveloping.ecobin.device.api.port.ScheduleFullnessSampleDevicePort;
import org.enveloping.ecobin.device.api.port.TrustedDeviceTransportPresencePort;
import org.enveloping.ecobin.device.api.result.DeliveryCompletionResultReference;
import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskProofPort;
import org.enveloping.ecobin.framework.reliability.UntrustedInboxSourceException;
import org.enveloping.ecobin.recycling.api.port.ApplyCleanCompleteUseCase;
import org.enveloping.ecobin.recycling.infrastructure.fullness.TransactionBoundFullnessDetectionCommandRef;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.function.Function;
import java.util.stream.Collectors;

/**
 * Applies the trusted cleaner-confirmed normal completion in one transaction.
 * Device evidence is immutable; the generated clean record is the editable
 * business projection and never enters a review or reward flow.
 */
@Service
public class ApplyCleanCompleteService
        implements ApplyCleanCompleteUseCase {

    private static final String MESSAGE_KIND = "CLEAN_COMPLETE";
    private static final String TASK_TYPE = "START_CLEAN_OPERATION";
    private static final String TARGET_TYPE = "CLEAN_OPERATION";
    private static final Set<String> PHOTO_POSITIONS = Set.of(
            "FIRST_OPEN_INNER",
            "FIRST_OPEN_OUTER",
            "FINAL_CLOSE_INNER",
            "FINAL_CLOSE_OUTER");

    static final String FIND_EDGE_COLLISIONS_SQL = """
            SELECT event_uid, deployment_id, edge_event_sequence,
                   LOWER(HEX(canonical_sha256)) AS canonical_sha256,
                   source_inbox_id
            FROM dev_edge_event
            WHERE event_uid = ?
               OR (deployment_id = ? AND edge_event_sequence = ?)
               OR source_inbox_id = ?
            """;

    static final String OPERATION_LOCK_CLAUSE =
            "FOR UPDATE OF operation";
    static final String CAPACITY_NO_OP_DUPLICATE_CLAUSE = """
            ON DUPLICATE KEY UPDATE
                updated_at = rec_port_capacity_state.updated_at
            """;
    static final String DELETE_RESERVED_BAG_SLOT_SQL = """
            DELETE FROM rec_bag_current_occupancy
            WHERE tenant_id = ?
              AND organization_id = ?
              AND bag_id = ?
              AND occupancy_type = 'CLEAN_RESERVED'
              AND clean_operation_id = ?
            """;
    static final String INSERT_PORT_BOUND_BAG_SLOT_SQL = """
            INSERT INTO rec_bag_current_occupancy (
                bag_id, tenant_id, organization_id,
                occupancy_type, port_id,
                clean_operation_id, acquired_at
            ) VALUES (?, ?, ?, 'PORT_BOUND', ?, NULL, ?)
            """;

    static boolean isTerminalPhotoState(String status) {
        return Set.of(
                "AVAILABLE",
                "PERMANENTLY_MISSING").contains(status);
    }

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final ScheduleFullnessSampleDevicePort fullnessSamples;
    private final ReliableDeviceTaskProofPort taskProofPort;
    private final ReliableEdgeConfirmationPort confirmationPort;
    private final TrustedDeviceTransportPresencePort transportPresence;

    public ApplyCleanCompleteService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            ScheduleFullnessSampleDevicePort fullnessSamples,
            ReliableDeviceTaskProofPort taskProofPort,
            ReliableEdgeConfirmationPort confirmationPort,
            TrustedDeviceTransportPresencePort transportPresence) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.fullnessSamples = fullnessSamples;
        this.taskProofPort = taskProofPort;
        this.confirmationPort = confirmationPort;
        this.transportPresence = transportPresence;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public TrustedDeviceEventApplyResult apply(
            TrustedDeviceInboxEvent event) {
        if (!MESSAGE_KIND.equals(event.messageKind())
                || event.normalizedSchemaVersion() != 1) {
            throw new IllegalArgumentException(
                    "unsupported clean completion inbox message");
        }
        CleanFact fact = parse(event.normalizedPayload());
        requireNormalCompletion(fact);
        return event.sourceInbox().use(
                (inboxId, tenantId, organizationId) ->
                        complete(
                                fact,
                                inboxId,
                                tenantId,
                                organizationId));
    }

    private TrustedDeviceEventApplyResult complete(
            CleanFact fact,
            long inboxId,
            long tenantId,
            long organizationId) {
        Asset asset = lockAsset(fact.hardwareSn());
        Deployment deployment = lockDeployment(
                fact,
                asset.id(),
                tenantId,
                organizationId);
        transportPresence.observeAuthenticatedMessage(
                fact.hardwareSn(), inboxId);
        lockDeploymentRuntime(
                deployment.id(), tenantId, organizationId);
        Operation operation = lockOperation(
                fact,
                deployment,
                tenantId,
                organizationId);

        if ("COMPLETED".equals(operation.status())) {
            return requirePreviouslyApplied(
                    fact,
                    inboxId,
                    deployment.id(),
                    operation);
        }
        if (!Set.of(
                "PREPARED", "EDGE_SAVED", "IN_PROGRESS",
                "RECOVERY_REQUIRED").contains(operation.status())) {
            throw untrusted("clean operation is no longer completable");
        }

        lockPortRuntime(operation);
        lockDeviceOccupancy(asset.id(), operation);
        lockBagOccupancies(operation);
        Command command = lockStartCommand(fact, operation);
        verifyFrozenFacts(fact, operation, command);

        List<ExistingEdge> collisions = findEdgeCollisions(
                fact, deployment.id(), inboxId);
        if (!collisions.isEmpty()) {
            throw untrusted(
                    "clean completion identity or sequence conflicts");
        }

        LocalDateTime receivedAt = databaseNow();
        long edgeEventId = insertEdgeEvent(
                fact,
                inboxId,
                tenantId,
                organizationId,
                deployment.id(),
                receivedAt);
        long physicalResultId = insertPhysicalResult(
                fact,
                operation,
                command,
                edgeEventId,
                receivedAt);

        Capacity capacity = lockOrInitializeCapacity(
                operation, receivedAt);
        long visibilitySequence = nextVisibilitySequence(
                tenantId, organizationId, receivedAt);
        Calculation calculation = calculate(fact, operation);
        String recordNo = cleanRecordNo(operation.uid());
        long cleanRecordId = insertCleanRecord(
                fact,
                operation,
                physicalResultId,
                visibilitySequence,
                recordNo,
                calculation,
                receivedAt);
        insertAnomalies(
                fact,
                operation,
                cleanRecordId,
                physicalResultId,
                calculation,
                receivedAt);
        mergePhotos(fact, operation, receivedAt);

        BagSwap swap = swapBags(operation, receivedAt);
        Baseline baseline = establishBaseline(
                fact,
                operation,
                cleanRecordId,
                physicalResultId,
                swap.installedEventId(),
                receivedAt);
        Detection detection = createFullnessDetection(
                fact,
                operation,
                cleanRecordId,
                capacity,
                baseline,
                receivedAt);
        projectCapacity(
                fact,
                operation,
                capacity,
                baseline,
                detection,
                receivedAt);
        if (detection.samplingRequired()) {
            scheduleInitialFullnessSample(
                    fact,
                    operation,
                    baseline,
                    detection);
        }

        mergeStartCommandSuccess(command, receivedAt);
        taskProofPort.completeFromTrustedProof(
                TASK_TYPE,
                TARGET_TYPE,
                operation.uid().toString());
        completeOperation(
                operation,
                cleanRecordId,
                fact.preUnlockMeasurement().reportedWeightGrams(),
                receivedAt);
        releaseDeviceOccupancy(asset.id(), operation);
        touchRuntimeAndClearPendingDelivery(
                deployment.id(), operation, receivedAt);

        List<DeliveryCompletionResultReference> references =
                cleanCompletionResultReferences(
                        recordNo,
                        detection.uid());
        confirmationPort.registerApplied(
                tenantId,
                organizationId,
                deployment.id(),
                deployment.publicCode(),
                fact.eventUid().toString(),
                fact.payloadSha256(),
                "CREATED",
                references,
                receivedAt);
        return TrustedDeviceEventApplyResult.APPLIED;
    }

    static List<DeliveryCompletionResultReference>
            cleanCompletionResultReferences(
            String recordNo,
            UUID detectionUid) {
        return List.of(
                new DeliveryCompletionResultReference(
                        "CLEAN_RECORD", recordNo),
                new DeliveryCompletionResultReference(
                        "FULLNESS_DETECTION",
                        detectionUid.toString()));
    }

    private TrustedDeviceEventApplyResult requirePreviouslyApplied(
            CleanFact fact,
            long inboxId,
            long deploymentId,
            Operation operation) {
        verifyFrozenFacts(fact, operation, lockStartCommand(fact, operation));
        List<ExistingEdge> collisions = findEdgeCollisions(
                fact, deploymentId, inboxId);
        if (operation.completionRecordId() != null
                && collisions.size() == 1
                && collisions.getFirst().matches(
                fact, deploymentId, inboxId)) {
            return TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED;
        }
        throw untrusted("completed clean event identity conflicts");
    }

    private Asset lockAsset(String hardwareSn) {
        List<Asset> rows = jdbc.query("""
                        SELECT id
                        FROM dev_device_asset
                        WHERE hardware_sn = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new Asset(rs.getLong("id")),
                hardwareSn);
        if (rows.size() != 1) {
            throw untrusted("clean source device is unknown");
        }
        return rows.getFirst();
    }

    private Deployment lockDeployment(
            CleanFact fact,
            long assetId,
            long tenantId,
            long organizationId) {
        List<Deployment> rows = jdbc.query("""
                        SELECT id, public_code
                        FROM dev_device_deployment
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND public_code = ?
                          AND lifecycle_status = 'ENABLED'
                        FOR UPDATE
                        """,
                (rs, ignored) -> new Deployment(
                        rs.getLong("id"),
                        rs.getString("public_code")),
                tenantId,
                organizationId,
                assetId,
                fact.deploymentCode());
        if (rows.size() != 1) {
            throw untrusted("clean deployment scope differs");
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
            throw untrusted("clean deployment runtime is missing");
        }
    }

    private Operation lockOperation(
            CleanFact fact,
            Deployment deployment,
            long tenantId,
            long organizationId) {
        List<Operation> rows = jdbc.query("""
                        SELECT operation.id,
                               operation.operation_uid,
                               operation.tenant_id,
                               operation.organization_id,
                               operation.deployment_id,
                               operation.port_id,
                               port.port_no,
                               operation.cleaner_organization_user_id,
                               operation.device_config_version_id,
                               config.version_no AS config_version_no,
                               config.content_sha256 AS config_content_sha256,
                               config.mcu_payload_sha256 AS config_mcu_sha256,
                               snapshot.id AS port_config_snapshot_id,
                               snapshot.fullness_mode,
                               snapshot.configured_full_weight_g,
                               snapshot.fullness_settle_wait_ms,
                               snapshot.fullness_confirmation_wait_ms,
                               snapshot.weight_measurement_timeout_ms,
                               snapshot.weight_minimum_g,
                               snapshot.weight_maximum_g,
                               snapshot.calibration_version,
                               operation.clean_config_version_id,
                               operation.clean_config_version_no,
                               operation.old_bag_binding_state,
                               operation.old_bag_id,
                               operation.old_bag_code_snapshot,
                               old_bag.bag_uid AS old_bag_uid,
                               operation.old_baseline_state,
                               operation.old_baseline_id,
                               operation.old_baseline_weight_g,
                               operation.new_bag_id,
                               operation.new_bag_code_snapshot,
                               new_bag.bag_uid AS new_bag_uid,
                               operation.pending_delivery_result_session_id,
                               operation.status,
                               operation.completion_record_id
                        FROM rec_clean_operation operation
                        JOIN dev_port port
                          ON port.tenant_id = operation.tenant_id
                         AND port.organization_id = operation.organization_id
                         AND port.deployment_id = operation.deployment_id
                         AND port.id = operation.port_id
                        JOIN dev_config_version config
                          ON config.tenant_id = operation.tenant_id
                         AND config.organization_id = operation.organization_id
                         AND config.deployment_id = operation.deployment_id
                         AND config.id = operation.device_config_version_id
                        JOIN dev_port_config_snapshot snapshot
                          ON snapshot.tenant_id = operation.tenant_id
                         AND snapshot.organization_id = operation.organization_id
                         AND snapshot.deployment_id = operation.deployment_id
                         AND snapshot.config_version_id =
                             operation.device_config_version_id
                         AND snapshot.port_id = operation.port_id
                        LEFT JOIN rec_bag old_bag
                          ON old_bag.tenant_id = operation.tenant_id
                         AND old_bag.organization_id = operation.organization_id
                         AND old_bag.id = operation.old_bag_id
                        JOIN rec_bag new_bag
                          ON new_bag.tenant_id = operation.tenant_id
                         AND new_bag.organization_id = operation.organization_id
                         AND new_bag.id = operation.new_bag_id
                        WHERE operation.operation_uid = ?
                          AND operation.tenant_id = ?
                          AND operation.organization_id = ?
                          AND operation.deployment_id = ?
                        %s
                        """.formatted(OPERATION_LOCK_CLAUSE),
                (rs, ignored) -> operation(rs),
                fact.operationUid().toString(),
                tenantId,
                organizationId,
                deployment.id());
        if (rows.size() != 1) {
            throw untrusted("clean operation is unknown in source scope");
        }
        return rows.getFirst();
    }

    private void lockPortRuntime(Operation operation) {
        List<Long> rows = jdbc.query("""
                        SELECT port_id
                        FROM dev_port_runtime_state
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND port_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("port_id"),
                operation.tenantId(),
                operation.organizationId(),
                operation.deploymentId(),
                operation.portId());
        if (rows.size() != 1) {
            throw untrusted("clean port runtime is missing");
        }
    }

    private void lockDeviceOccupancy(
            long assetId,
            Operation operation) {
        List<Long> rows = jdbc.query("""
                        SELECT clean_operation_id
                        FROM dev_device_occupancy
                        WHERE asset_id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND occupancy_kind = 'CLEAN'
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("clean_operation_id"),
                assetId,
                operation.tenantId(),
                operation.organizationId(),
                operation.deploymentId());
        if (rows.size() != 1
                || rows.getFirst() != operation.id()) {
            throw untrusted("clean device occupancy differs");
        }
    }

    private void lockBagOccupancies(Operation operation) {
        if ("BOUND".equals(operation.oldBagBindingState())) {
            List<Long> oldRows = jdbc.query("""
                            SELECT bag_id
                            FROM rec_bag_current_occupancy
                            WHERE tenant_id = ?
                              AND organization_id = ?
                              AND bag_id = ?
                              AND occupancy_type = 'PORT_BOUND'
                              AND port_id = ?
                            FOR UPDATE
                            """,
                    (rs, ignored) -> rs.getLong("bag_id"),
                    operation.tenantId(),
                    operation.organizationId(),
                    operation.oldBagId(),
                    operation.portId());
            if (oldRows.size() != 1) {
                throw untrusted("old clean bag binding has changed");
            }
        }
        List<Long> newRows = jdbc.query("""
                        SELECT bag_id
                        FROM rec_bag_current_occupancy
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND bag_id = ?
                          AND occupancy_type = 'CLEAN_RESERVED'
                          AND clean_operation_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("bag_id"),
                operation.tenantId(),
                operation.organizationId(),
                operation.newBagId(),
                operation.id());
        if (newRows.size() != 1) {
            throw untrusted("new clean bag reservation has changed");
        }
    }

    private Command lockStartCommand(
            CleanFact fact,
            Operation operation) {
        List<Command> rows = jdbc.query("""
                        SELECT id, command_uid, physical_state
                        FROM dev_device_command
                        WHERE command_uid = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND command_type = 'START_CLEAN_OPERATION'
                          AND clean_operation_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new Command(
                        rs.getLong("id"),
                        UUID.fromString(rs.getString("command_uid")),
                        rs.getString("physical_state")),
                fact.commandUid().toString(),
                operation.tenantId(),
                operation.organizationId(),
                operation.deploymentId(),
                operation.id());
        if (rows.size() != 1) {
            throw untrusted("clean command differs");
        }
        return rows.getFirst();
    }

    private static void verifyFrozenFacts(
            CleanFact fact,
            Operation operation,
            Command command) {
        boolean oldBagMatches =
                (operation.oldBagUid() == null
                        && fact.oldBagUid() == null)
                        || (operation.oldBagUid() != null
                        && operation.oldBagUid().equals(
                        fact.oldBagUid()));
        Measurement pre = fact.preUnlockMeasurement();
        Measurement finalMeasurement = fact.finalMeasurement();
        if (!command.uid().equals(fact.commandUid())
                || operation.portNo() != fact.portNo()
                || !operation.newBagUid().equals(fact.newBagUid())
                || !oldBagMatches
                || operation.configVersionNo()
                != fact.configurationVersion()
                || !Arrays.equals(
                operation.configContentSha256(),
                digest(fact.configurationContentSha256()))
                || !Arrays.equals(
                operation.configMcuSha256(),
                digest(fact.configurationMcuPayloadSha256()))
                || pre.calibrationVersion()
                != operation.calibrationVersion()
                || !hasAtLeastOneReportedSample(pre.sampleCount())
                || pre.reportedWeightGrams()
                < operation.weightMinimumGrams()
                || pre.reportedWeightGrams()
                > operation.weightMaximumGrams()
                || finalMeasurement.calibrationVersion()
                != operation.calibrationVersion()
                || ("STABLE".equals(finalMeasurement.status())
                && (!hasAtLeastOneReportedSample(
                finalMeasurement.sampleCount())
                || finalMeasurement.reportedWeightGrams()
                < operation.weightMinimumGrams()
                || finalMeasurement.reportedWeightGrams()
                > operation.weightMaximumGrams()))) {
            throw untrusted("clean completion frozen facts differ");
        }
    }

    private List<ExistingEdge> findEdgeCollisions(
            CleanFact fact,
            long deploymentId,
            long inboxId) {
        return jdbc.query(
                FIND_EDGE_COLLISIONS_SQL,
                (rs, ignored) -> new ExistingEdge(
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

    static boolean hasAtLeastOneReportedSample(int sampleCount) {
        return sampleCount >= 1;
    }

    private long insertEdgeEvent(
            CleanFact fact,
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
                            'CLEAN_COMPLETE', 'RELIABLE_FACT', 1,
                            'CLEAN_OPERATION', ?,
                            ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                fact.eventUid().toString(),
                tenantId,
                organizationId,
                deploymentId,
                fact.edgeEventSequence(),
                sha256(fact.operationUid().toString()),
                utc(fact.deviceOccurredAt()),
                fact.clockQuality(),
                receivedAt,
                digest(fact.payloadSha256()),
                digest(fact.canonicalSha256()),
                inboxId,
                receivedAt),
                "insert clean edge event");
        return requiredId(
                "SELECT id FROM dev_edge_event WHERE event_uid = ?",
                fact.eventUid().toString(),
                "clean edge event");
    }

    private long insertPhysicalResult(
            CleanFact fact,
            Operation operation,
            Command command,
            long edgeEventId,
            LocalDateTime receivedAt) {
        Measurement pre = fact.preUnlockMeasurement();
        Measurement finalMeasurement = fact.finalMeasurement();
        boolean stableFinal = "STABLE".equals(
                finalMeasurement.status());
        Long finalWeight = stableFinal
                ? finalMeasurement.reportedWeightGrams()
                : null;
        Long finalLastObserved = !stableFinal
                && finalMeasurement.weightValueAvailable()
                ? finalMeasurement.reportedWeightGrams()
                : null;
        requireSingle(jdbc.update("""
                        INSERT INTO dev_physical_result (
                            tenant_id, organization_id, deployment_id,
                            port_id, edge_event_id, edge_event_type,
                            command_id, command_type,
                            reported_config_version_no,
                            reported_config_content_sha256,
                            reported_config_mcu_payload_sha256,
                            result_type, clean_operation_id,
                            clean_pre_measurement_uid,
                            clean_pre_measurement_status,
                            clean_pre_weight_g,
                            clean_pre_last_observed_weight_g,
                            clean_pre_weight_value_available,
                            clean_pre_weight_value_kind,
                            clean_pre_measurement_elapsed_ms,
                            clean_pre_sample_count,
                            clean_pre_calibration_version,
                            clean_pre_sensor_health,
                            clean_pre_fault_code,
                            clean_pre_mcu_boot_id,
                            clean_pre_mcu_event_sequence,
                            clean_final_measurement_uid,
                            clean_final_measurement_status,
                            clean_final_weight_g,
                            clean_final_last_observed_weight_g,
                            clean_final_weight_value_available,
                            clean_final_weight_value_kind,
                            clean_final_measurement_elapsed_ms,
                            clean_final_sample_count,
                            clean_final_calibration_version,
                            clean_final_sensor_health,
                            clean_final_fault_code,
                            clean_final_mcu_boot_id,
                            clean_final_mcu_event_sequence,
                            clean_removed_net_weight_g,
                            clean_new_baseline_weight_g,
                            cleaner_completion_confirmed,
                            cleaner_physical_close_confirmed,
                            clean_action_sequence,
                            clean_lock_power_state,
                            clean_solenoid_health,
                            clean_door_inferred_state,
                            clean_door_state_basis,
                            created_at
                        ) VALUES (
                            ?, ?, ?, ?,
                            ?, 'CLEAN_COMPLETE',
                            ?, 'START_CLEAN_OPERATION',
                            ?, ?, ?,
                            'CLEAN', ?,
                            ?, 'STABLE', ?, NULL, 1,
                            'STABLE_WINDOW_MEAN',
                            ?, ?, ?, 'OK', NULL, ?, ?,
                            ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, 1, 1, ?,
                            'DEENERGIZED', ?, 'UNKNOWN',
                            'CLEANER_CONFIRMATION', ?
                        )
                        """,
                operation.tenantId(),
                operation.organizationId(),
                operation.deploymentId(),
                operation.portId(),
                edgeEventId,
                command.id(),
                operation.configVersionNo(),
                operation.configContentSha256(),
                operation.configMcuSha256(),
                operation.id(),
                pre.measurementUid().toString(),
                pre.reportedWeightGrams(),
                pre.measurementElapsedMs(),
                pre.sampleCount(),
                pre.calibrationVersion(),
                pre.mcuBootId(),
                pre.mcuEventSequence(),
                finalMeasurement.measurementUid().toString(),
                finalMeasurement.status(),
                finalWeight,
                finalLastObserved,
                finalMeasurement.weightValueAvailable(),
                finalMeasurement.weightValueKind(),
                finalMeasurement.measurementElapsedMs(),
                finalMeasurement.sampleCount(),
                finalMeasurement.calibrationVersion(),
                finalMeasurement.sensorHealth(),
                finalMeasurement.faultCode(),
                finalMeasurement.mcuBootId(),
                finalMeasurement.mcuEventSequence(),
                fact.removedNetWeightGrams(),
                fact.newBaselineWeightGrams(),
                fact.actionSequence(),
                fact.solenoidHealth(),
                receivedAt),
                "insert clean physical result");
        return requiredId("""
                        SELECT id
                        FROM dev_physical_result
                        WHERE clean_operation_id = ?
                        """,
                operation.id(),
                "clean physical result");
    }

    private Capacity lockOrInitializeCapacity(
            Operation operation,
            LocalDateTime now) {
        byte[] generatedRule = fullnessRuleFingerprint(operation);
        requireSingleOrInserted(jdbc.update("""
                        INSERT INTO rec_port_capacity_state (
                            port_id, tenant_id, organization_id,
                            deployment_id, baseline_state,
                            current_baseline_id,
                            current_baseline_weight_g,
                            latest_stable_total_weight_g,
                            raw_net_weight_g,
                            displayed_fullness_percent,
                            detection_gate, current_detection_id,
                            current_rule_fingerprint,
                            confirmed_fullness_state,
                            last_detection_id,
                            current_fullness_event_id,
                            lock_version, updated_at
                        ) VALUES (
                            ?, ?, ?, ?,
                            ?, ?, ?,
                            NULL, NULL, NULL,
                            'UNKNOWN', NULL, ?, 'UNKNOWN',
                            NULL, NULL, 0, ?
                        )
                        %s
                        """.formatted(
                        CAPACITY_NO_OP_DUPLICATE_CLAUSE),
                operation.portId(),
                operation.tenantId(),
                operation.organizationId(),
                operation.deploymentId(),
                "TRUSTED".equals(operation.oldBaselineState())
                        ? "VALID" : "UNINITIALIZED",
                "TRUSTED".equals(operation.oldBaselineState())
                        ? operation.oldBaselineId() : null,
                "TRUSTED".equals(operation.oldBaselineState())
                        ? operation.oldBaselineWeightGrams() : null,
                generatedRule,
                now),
                "ensure clean capacity state");
        List<Capacity> rows = jdbc.query("""
                        SELECT current_detection_id,
                               current_rule_fingerprint,
                               lock_version
                        FROM rec_port_capacity_state
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND port_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new Capacity(
                        nullableLong(rs, "current_detection_id"),
                        rs.getBytes("current_rule_fingerprint"),
                        rs.getLong("lock_version")),
                operation.tenantId(),
                operation.organizationId(),
                operation.deploymentId(),
                operation.portId());
        if (rows.size() != 1
                || rows.getFirst().currentDetectionId() != null) {
            throw untrusted("clean fullness generation has changed");
        }
        Capacity capacity = rows.getFirst();
        if (capacity.ruleFingerprint() == null) {
            requireSingle(jdbc.update("""
                            UPDATE rec_port_capacity_state
                            SET current_rule_fingerprint = ?,
                                lock_version = lock_version + 1,
                                updated_at = ?
                            WHERE port_id = ?
                              AND lock_version = ?
                            """,
                    generatedRule,
                    now,
                    operation.portId(),
                    capacity.lockVersion()),
                    "freeze clean fullness rule");
            return new Capacity(
                    null,
                    generatedRule,
                    capacity.lockVersion() + 1);
        }
        return capacity;
    }

    private long nextVisibilitySequence(
            long tenantId,
            long organizationId,
            LocalDateTime now) {
        List<Long> rows = jdbc.query("""
                        SELECT last_visibility_sequence_no
                        FROM rec_organization_clean_record_counter
                        WHERE tenant_id = ?
                          AND organization_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong(
                        "last_visibility_sequence_no"),
                tenantId,
                organizationId);
        if (rows.size() != 1
                || rows.getFirst() >= 9_007_199_254_740_991L) {
            throw new IllegalStateException(
                    "organization clean record counter is unavailable");
        }
        long current = rows.getFirst();
        long next = current + 1;
        requireSingle(jdbc.update("""
                        UPDATE rec_organization_clean_record_counter
                        SET last_visibility_sequence_no = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND last_visibility_sequence_no = ?
                        """,
                next,
                now,
                tenantId,
                organizationId,
                current),
                "advance clean record counter");
        return next;
    }

    private long insertCleanRecord(
            CleanFact fact,
            Operation operation,
            long physicalResultId,
            long visibilitySequence,
            String recordNo,
            Calculation calculation,
            LocalDateTime now) {
        requireSingle(jdbc.update("""
                        INSERT INTO rec_clean_record (
                            clean_record_no,
                            tenant_id, organization_id,
                            visibility_sequence_no,
                            clean_operation_id, physical_result_id,
                            deployment_id, port_id,
                            cleaner_organization_user_id,
                            clean_config_version_id,
                            clean_config_version_no,
                            old_bag_binding_state,
                            old_baseline_state,
                            old_bag_id, old_bag_code_snapshot,
                            new_bag_id, new_bag_code_snapshot,
                            pre_unlock_weight_status,
                            pre_unlock_weight_g,
                            old_baseline_weight_g,
                            device_removed_net_weight_status,
                            device_removed_net_weight_g,
                            recalculated_removed_net_weight_status,
                            recalculated_removed_net_weight_g,
                            final_total_weight_status,
                            final_total_weight_g,
                            effective_removed_net_weight_g,
                            effective_weight_source,
                            record_remark, lock_version,
                            record_class,
                            device_occurred_at,
                            backend_received_at,
                            completed_at, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?,
                            'RELIABLE', ?, ?,
                            ?, ?, ?, ?, ?, ?,
                            ?, 'DEVICE_RECALCULATED', NULL, 1,
                            ?, ?, ?, ?, ?, ?
                        )
                        """,
                recordNo,
                operation.tenantId(),
                operation.organizationId(),
                visibilitySequence,
                operation.id(),
                physicalResultId,
                operation.deploymentId(),
                operation.portId(),
                operation.cleanerOrganizationUserId(),
                operation.cleanConfigVersionId(),
                operation.cleanConfigVersionNo(),
                operation.oldBagBindingState(),
                operation.oldBaselineState(),
                operation.oldBagId(),
                operation.oldBagCode(),
                operation.newBagId(),
                operation.newBagCode(),
                fact.preUnlockMeasurement().reportedWeightGrams(),
                operation.oldBaselineWeightGrams(),
                calculation.deviceStatus(),
                fact.removedNetWeightGrams(),
                calculation.recalculatedStatus(),
                calculation.recalculatedWeightGrams(),
                calculation.finalStatus(),
                calculation.finalWeightGrams(),
                "RELIABLE".equals(calculation.recalculatedStatus())
                        ? calculation.recalculatedWeightGrams()
                        : null,
                calculation.recordClass(),
                utc(fact.deviceOccurredAt()),
                now,
                now,
                now,
                now),
                "insert clean record");
        return requiredId("""
                        SELECT id
                        FROM rec_clean_record
                        WHERE clean_record_no = ?
                        """,
                recordNo,
                "clean record");
    }

    private void insertAnomalies(
            CleanFact fact,
            Operation operation,
            long cleanRecordId,
            long physicalResultId,
            Calculation calculation,
            LocalDateTime now) {
        if (fact.removedNetWeightGrams() == null) {
            insertAnomaly(
                    operation,
                    cleanRecordId,
                    "DEVICE_REMOVED_WEIGHT_UNAVAILABLE",
                    null,
                    now);
        } else if (calculation.recalculatedWeightGrams() != null
                && !fact.removedNetWeightGrams().equals(
                calculation.recalculatedWeightGrams())) {
            insertAnomaly(
                    operation,
                    cleanRecordId,
                    "REMOVED_WEIGHT_MISMATCH",
                    Map.of(
                            "reportedWeightGrams",
                            fact.removedNetWeightGrams(),
                            "recalculatedWeightGrams",
                            calculation.recalculatedWeightGrams()),
                    now);
        }
        if (!"RELIABLE".equals(
                calculation.recalculatedStatus())) {
            insertAnomaly(
                    operation,
                    cleanRecordId,
                    "RECALCULATED_REMOVED_WEIGHT_"
                            + calculation.recalculatedStatus(),
                    calculation.recalculatedWeightGrams() == null
                            ? null
                            : Map.of(
                            "weightGrams",
                            calculation.recalculatedWeightGrams()),
                    now);
        }
        if (!"RELIABLE".equals(calculation.finalStatus())) {
            Map<String, Object> diagnostic = new LinkedHashMap<>();
            diagnostic.put(
                    "measurementStatus",
                    fact.finalMeasurement().status());
            diagnostic.put(
                    "faultCode",
                    fact.finalMeasurement().faultCode());
            diagnostic.put(
                    "physicalResultId",
                    physicalResultId);
            insertAnomaly(
                    operation,
                    cleanRecordId,
                    "NEW_BASELINE_" + calculation.finalStatus(),
                    diagnostic,
                    now);
        }
    }

    private void insertAnomaly(
            Operation operation,
            long cleanRecordId,
            String code,
            Map<String, Object> diagnostic,
            LocalDateTime now) {
        requireSingle(jdbc.update("""
                        INSERT INTO rec_clean_anomaly (
                            tenant_id, organization_id,
                            clean_record_id, anomaly_code,
                            diagnostic_json, detected_at, created_at
                        ) VALUES (?, ?, ?, ?, CAST(? AS JSON), ?, ?)
                        """,
                operation.tenantId(),
                operation.organizationId(),
                cleanRecordId,
                code,
                diagnostic == null
                        ? null
                        : objectMapper.writeValueAsString(diagnostic),
                now,
                now),
                "insert clean anomaly " + code);
    }

    private void mergePhotos(
            CleanFact fact,
            Operation operation,
            LocalDateTime now) {
        Map<String, CleanPhoto> photos = fact.photos().stream()
                .collect(Collectors.toMap(
                        CleanPhoto::slot,
                        Function.identity()));
        if (!photos.keySet().equals(PHOTO_POSITIONS)) {
            throw untrusted("clean photo slots are incomplete");
        }
        for (String position : PHOTO_POSITIONS) {
            CleanPhoto photo = photos.get(position);
            PhotoSlot target = lockPhotoSlot(operation, position);
            if (target.terminal()) {
                // PHOTO_STATUS_REPORTED is a later lifecycle fact than the
                // completion snapshot. Preserve a terminal slot that was
                // already applied independently.
                continue;
            }
            if (!"UPLOAD_PENDING".equals(target.status())) {
                throw new IllegalStateException(
                        "clean photo slot has unsupported state "
                                + position + ": " + target.status());
            }
            requireSingle(jdbc.update("""
                            UPDATE rec_clean_photo
                            SET photo_uid = ?, status = ?, object_url = ?,
                                sha256 = ?, size_bytes = ?, captured_at = ?,
                                linked_at = ?, missing_reason = ?,
                                updated_at = ?
                            WHERE id = ?
                              AND status = 'UPLOAD_PENDING'
                            """,
                    photo.photoUid() == null
                            ? null : photo.photoUid().toString(),
                    photo.status(),
                    photo.url(),
                    nullableDigest(photo.sha256()),
                    photo.sizeBytes(),
                    utc(photo.capturedAt()),
                    "UPLOAD_PENDING".equals(photo.status())
                            ? null : now,
                    photo.missingReason(),
                    now,
                    target.id()),
                    "merge clean photo " + position);
        }
    }

    private PhotoSlot lockPhotoSlot(
            Operation operation,
            String position) {
        List<PhotoSlot> rows = jdbc.query("""
                        SELECT id, status
                        FROM rec_clean_photo
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND clean_operation_id = ?
                          AND position = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new PhotoSlot(
                        rs.getLong("id"),
                        rs.getString("status")),
                operation.tenantId(),
                operation.organizationId(),
                operation.id(),
                position);
        if (rows.size() != 1) {
            throw new IllegalStateException(
                    "clean photo slot " + position + " mapped to "
                            + rows.size() + " rows");
        }
        return rows.getFirst();
    }

    private BagSwap swapBags(
            Operation operation,
            LocalDateTime now) {
        if ("BOUND".equals(operation.oldBagBindingState())) {
            requireSingle(jdbc.update("""
                            DELETE FROM rec_bag_current_occupancy
                            WHERE tenant_id = ?
                              AND organization_id = ?
                              AND bag_id = ?
                              AND occupancy_type = 'PORT_BOUND'
                              AND port_id = ?
                            """,
                    operation.tenantId(),
                    operation.organizationId(),
                    operation.oldBagId(),
                    operation.portId()),
                    "remove old clean bag binding");
            insertBagEvent(
                    operation,
                    operation.oldBagId(),
                    "REMOVED_BY_CLEAN",
                    now);
        }
        requireSingle(jdbc.update(
                        DELETE_RESERVED_BAG_SLOT_SQL,
                operation.tenantId(),
                operation.organizationId(),
                operation.newBagId(),
                operation.id()),
                "remove reserved clean bag slot");
        requireSingle(jdbc.update(
                        INSERT_PORT_BOUND_BAG_SLOT_SQL,
                operation.newBagId(),
                operation.tenantId(),
                operation.organizationId(),
                operation.portId(),
                now),
                "install reserved clean bag");
        long installedEventId = insertBagEvent(
                operation,
                operation.newBagId(),
                "INSTALLED_BY_CLEAN",
                now);
        return new BagSwap(installedEventId);
    }

    private long insertBagEvent(
            Operation operation,
            Long bagId,
            String type,
            LocalDateTime now) {
        UUID eventUid = UUID.randomUUID();
        requireSingle(jdbc.update("""
                        INSERT INTO rec_bag_occupancy_event (
                            event_uid, tenant_id, organization_id,
                            bag_id, port_id, clean_operation_id,
                            event_type, occurred_at, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                eventUid.toString(),
                operation.tenantId(),
                operation.organizationId(),
                bagId,
                operation.portId(),
                operation.id(),
                type,
                now,
                now),
                "insert clean bag event " + type);
        return requiredId("""
                        SELECT id
                        FROM rec_bag_occupancy_event
                        WHERE event_uid = ?
                        """,
                eventUid.toString(),
                "clean bag event");
    }

    private Baseline establishBaseline(
            CleanFact fact,
            Operation operation,
            long cleanRecordId,
            long physicalResultId,
            long installedEventId,
            LocalDateTime now) {
        if (!"STABLE".equals(fact.finalMeasurement().status())
                || fact.newBaselineWeightGrams() == null
                || fact.newBaselineWeightGrams() < 0) {
            return new Baseline(
                    null, null, "INVALID");
        }
        Long previous = jdbc.queryForObject("""
                        SELECT COALESCE(MAX(version_no), 0)
                        FROM rec_port_weight_baseline
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND port_id = ?
                        """,
                Long.class,
                operation.tenantId(),
                operation.organizationId(),
                operation.portId());
        long version = Math.addExact(
                previous == null ? 0L : previous,
                1L);
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
                            'CLEAN_COMPLETE', ?, ?, ?, NULL,
                            ?, ?, ?
                        )
                        """,
                operation.tenantId(),
                operation.organizationId(),
                operation.portId(),
                operation.newBagId(),
                version,
                installedEventId,
                physicalResultId,
                cleanRecordId,
                fact.newBaselineWeightGrams(),
                now,
                now),
                "establish clean baseline");
        long id = requiredId("""
                        SELECT id
                        FROM rec_port_weight_baseline
                        WHERE source_clean_record_id = ?
                        """,
                cleanRecordId,
                "clean baseline");
        return new Baseline(
                id,
                fact.newBaselineWeightGrams(),
                "VALID");
    }

    private Detection createFullnessDetection(
            CleanFact fact,
            Operation operation,
            long cleanRecordId,
            Capacity capacity,
            Baseline baseline,
            LocalDateTime now) {
        UUID detectionUid = UUID.randomUUID();
        boolean immediateBaselineFailure =
                "WEIGHT_ONLY".equals(operation.fullnessMode())
                        && !"VALID".equals(baseline.state());
        LocalDateTime nextSampleAt = immediateBaselineFailure
                ? null
                : now.plus(Duration.ofMillis(
                        operation.fullnessSettleWaitMs()));
        requireSingle(jdbc.update("""
                        INSERT INTO rec_fullness_detection (
                            detection_uid,
                            tenant_id, organization_id,
                            deployment_id, port_id,
                            trigger_type,
                            delivery_order_id, clean_record_id,
                            initiator_kind,
                            platform_admin_id, staff_account_id,
                            bag_id,
                            baseline_state_snapshot,
                            baseline_id_snapshot,
                            baseline_weight_g_snapshot,
                            device_config_version_id,
                            port_config_snapshot_id,
                            rule_fingerprint,
                            decision_mode,
                            configured_full_weight_g,
                            settle_wait_ms,
                            confirmation_wait_ms,
                            measurement_timeout_ms,
                            calculation_basis,
                            status, final_result, failure_code,
                            disposition,
                            initial_sample_id,
                            initial_sample_conclusion,
                            terminal_sample_id,
                            terminal_sample_conclusion,
                            next_sample_at, completed_at,
                            lock_version, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, ?, ?,
                            'CLEAN_COMPLETE', NULL, ?, NULL,
                            NULL, NULL, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            'FIXED_FRAME_TOTAL_WEIGHT',
                            ?, ?, ?, ?,
                            NULL, NULL, NULL, NULL,
                            ?, ?, 0, ?, ?
                        )
                        """,
                detectionUid.toString(),
                operation.tenantId(),
                operation.organizationId(),
                operation.deploymentId(),
                operation.portId(),
                cleanRecordId,
                operation.newBagId(),
                baseline.state(),
                baseline.id(),
                baseline.weightGrams(),
                operation.deviceConfigVersionId(),
                operation.portConfigSnapshotId(),
                capacity.ruleFingerprint(),
                operation.fullnessMode(),
                operation.configuredFullWeightGrams(),
                operation.fullnessSettleWaitMs(),
                operation.fullnessConfirmationWaitMs(),
                operation.measurementTimeoutMs(),
                immediateBaselineFailure
                        ? "FAILED" : "PENDING_INITIAL_SAMPLE",
                immediateBaselineFailure
                        ? "SOURCE_FAILED" : null,
                immediateBaselineFailure
                        ? "WEIGHT_BASELINE_UNAVAILABLE" : null,
                immediateBaselineFailure
                        ? "APPLIED" : "PENDING",
                nextSampleAt,
                immediateBaselineFailure ? now : null,
                now,
                now),
                "create post-clean fullness detection");
        long id = requiredId("""
                        SELECT id
                        FROM rec_fullness_detection
                        WHERE detection_uid = ?
                        """,
                detectionUid.toString(),
                "post-clean fullness detection");
        return new Detection(
                id,
                detectionUid,
                !immediateBaselineFailure);
    }

    private void projectCapacity(
            CleanFact fact,
            Operation operation,
            Capacity capacity,
            Baseline baseline,
            Detection detection,
            LocalDateTime now) {
        if (!detection.samplingRequired()) {
            requireSingle(jdbc.update("""
                            UPDATE rec_port_capacity_state
                            SET baseline_state = ?,
                                current_baseline_id = ?,
                                current_baseline_weight_g = ?,
                                latest_stable_total_weight_g = NULL,
                                raw_net_weight_g = NULL,
                                displayed_fullness_percent = NULL,
                                detection_gate = 'FAILED',
                                current_detection_id = NULL,
                                current_rule_fingerprint = ?,
                                confirmed_fullness_state = 'UNKNOWN',
                                last_detection_id = ?,
                                lock_version = lock_version + 1,
                                updated_at = ?
                            WHERE tenant_id = ?
                              AND organization_id = ?
                              AND deployment_id = ?
                              AND port_id = ?
                              AND lock_version = ?
                              AND current_detection_id IS NULL
                            """,
                    baseline.state(),
                    baseline.id(),
                    baseline.weightGrams(),
                    capacity.ruleFingerprint(),
                    detection.id(),
                    now,
                    operation.tenantId(),
                    operation.organizationId(),
                    operation.deploymentId(),
                    operation.portId(),
                    capacity.lockVersion()),
                    "project failed post-clean capacity gate");
            return;
        }
        requireSingle(jdbc.update("""
                        UPDATE rec_port_capacity_state
                        SET baseline_state = ?,
                            current_baseline_id = ?,
                            current_baseline_weight_g = ?,
                            latest_stable_total_weight_g = ?,
                            raw_net_weight_g = ?,
                            displayed_fullness_percent = ?,
                            detection_gate = 'PENDING',
                            current_detection_id = ?,
                            current_rule_fingerprint = ?,
                            confirmed_fullness_state = 'UNKNOWN',
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND port_id = ?
                          AND lock_version = ?
                          AND current_detection_id IS NULL
                        """,
                baseline.state(),
                baseline.id(),
                baseline.weightGrams(),
                baseline.weightGrams(),
                baseline.id() == null ? null : 0L,
                baseline.id() == null ? null : 0,
                detection.id(),
                capacity.ruleFingerprint(),
                now,
                operation.tenantId(),
                operation.organizationId(),
                operation.deploymentId(),
                operation.portId(),
                capacity.lockVersion()),
                "project post-clean capacity gate");
    }

    private void scheduleInitialFullnessSample(
            CleanFact fact,
            Operation operation,
            Baseline baseline,
            Detection detection) {
        fullnessSamples.schedule(new ScheduleFullnessSampleCommand(
                TransactionBoundFullnessDetectionCommandRef.issue(
                        operation.tenantId(),
                        operation.organizationId(),
                        operation.deploymentId(),
                        operation.portId(),
                        detection.id(),
                        operation.deviceConfigVersionId(),
                        operation.portConfigSnapshotId()),
                detection.uid(),
                operation.portNo(),
                "INITIAL",
                "CLEAN_COMPLETE",
                operation.fullnessMode(),
                baseline.weightGrams(),
                operation.configuredFullWeightGrams(),
                operation.fullnessSettleWaitMs(),
                operation.measurementTimeoutMs(),
                operation.configVersionNo(),
                HexFormat.of().formatHex(
                        operation.configContentSha256()),
                HexFormat.of().formatHex(
                        operation.configMcuSha256()),
                operation.uid(),
                fact.eventUid()));
    }

    private void mergeStartCommandSuccess(
            Command command,
            LocalDateTime completedAt) {
        if ("PHYSICAL_SUCCEEDED".equals(command.physicalState())) {
            return;
        }
        if (!Set.of(
                "QUEUED", "EDGE_ACCEPTED", "PHYSICAL_STARTED")
                .contains(command.physicalState())) {
            throw untrusted(
                    "clean completion conflicts with command state");
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
                "complete clean start command");
    }

    private void completeOperation(
            Operation operation,
            long cleanRecordId,
            long preWeightGrams,
            LocalDateTime now) {
        requireSingle(jdbc.update("""
                        UPDATE rec_clean_operation
                        SET status = 'COMPLETED',
                            edge_saved_confirmed = 1,
                            first_unlock_may_have_executed = 1,
                            clean_lock_deenergized_confirmed = 1,
                            cleaner_physical_close_confirmed = 1,
                            pre_unlock_weight_status = 'RELIABLE',
                            pre_unlock_weight_g = ?,
                            pre_unlock_weight_fault_code = NULL,
                            completion_record_id = ?,
                            ended_at = ?,
                            end_reason = 'CLEANER_CONFIRMED',
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND status IN (
                              'PREPARED', 'EDGE_SAVED', 'IN_PROGRESS',
                              'RECOVERY_REQUIRED'
                          )
                        """,
                preWeightGrams,
                cleanRecordId,
                now,
                now,
                operation.id(),
                operation.tenantId(),
                operation.organizationId()),
                "complete clean operation");
    }

    private void releaseDeviceOccupancy(
            long assetId,
            Operation operation) {
        requireSingle(jdbc.update("""
                        DELETE FROM dev_device_occupancy
                        WHERE asset_id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND occupancy_kind = 'CLEAN'
                          AND clean_operation_id = ?
                        """,
                assetId,
                operation.tenantId(),
                operation.organizationId(),
                operation.deploymentId(),
                operation.id()),
                "release clean device occupancy");
    }

    private void touchRuntimeAndClearPendingDelivery(
            long deploymentId,
            Operation operation,
            LocalDateTime now) {
        requireSingle(jdbc.update("""
                        UPDATE dev_port_runtime_state
                        SET pending_delivery_result_session_id =
                                CASE
                                    WHEN pending_delivery_result_session_id <=> ?
                                    THEN NULL
                                    ELSE pending_delivery_result_session_id
                                END,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND port_id = ?
                        """,
                operation.pendingDeliverySessionId(),
                now,
                operation.tenantId(),
                operation.organizationId(),
                deploymentId,
                operation.portId()),
                "clear clean pending delivery pointer");
        requireSingle(jdbc.update("""
                        UPDATE dev_deployment_runtime_state
                        SET last_device_event_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE deployment_id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                        """,
                now,
                now,
                deploymentId,
                operation.tenantId(),
                operation.organizationId()),
                "touch clean deployment runtime");
    }

    private CleanFact parse(String normalizedPayload) {
        JsonNode root = objectMapper.readTree(normalizedPayload);
        JsonNode source = requiredObject(root, "trustedSource");
        JsonNode event = requiredObject(root, "event");
        JsonNode target = requiredObject(event, "target");
        JsonNode payload = requiredObject(event, "payload");
        if (!MESSAGE_KIND.equals(requiredText(event, "eventType"))
                || !"RELIABLE_FACT".equals(
                requiredText(event, "deliveryClass"))
                || !TARGET_TYPE.equals(requiredText(target, "type"))) {
            throw new IllegalArgumentException(
                    "clean completion envelope constants differ");
        }
        UUID operationUid = uuid(payload, "operationUid");
        if (!operationUid.toString().equals(
                requiredText(target, "uid"))) {
            throw new IllegalArgumentException(
                    "clean target differs from operation");
        }
        JsonNode frozen = requiredObject(payload, "frozenConfig");
        JsonNode lock = requiredObject(
                payload,
                "cleanLockAndManualDoorConfirmation");
        List<CleanPhoto> photos = new ArrayList<>();
        requiredArray(payload, "photos")
                .forEach(node -> photos.add(photo(node)));
        return new CleanFact(
                uuid(event, "eventUid"),
                uuid(event, "commandUid"),
                operationUid,
                requiredText(source, "deviceName"),
                requiredText(event, "deploymentCode"),
                positiveLong(event, "edgeEventSequence"),
                nullableInstant(event, "occurredAt"),
                requiredText(event, "clockQuality"),
                requiredText(event, "payloadSha256"),
                requiredText(root, "eventCanonicalSha256"),
                Math.toIntExact(positiveLong(payload, "portNo")),
                nullableUuid(payload, "oldBagUid"),
                uuid(payload, "newBagUid"),
                measurement(requiredObject(
                        payload,
                        "preUnlockMeasurement")),
                measurement(requiredObject(
                        payload,
                        "cleanerConfirmedFinalMeasurement")),
                nullableLong(payload, "removedNetWeightGrams"),
                nullableLong(payload, "newBaselineWeightGrams"),
                requiredBoolean(
                        payload,
                        "cleanerCompletionConfirmed"),
                Math.toIntExact(positiveLong(
                        payload,
                        "cleanActionSequence")),
                requiredText(lock, "lockPowerState"),
                requiredText(lock, "solenoidHealth"),
                requiredText(lock, "physicalDoorStateBasis"),
                requiredBoolean(
                        lock,
                        "cleanerPhysicalCloseConfirmed"),
                positiveLong(frozen, "version"),
                requiredText(frozen, "contentSha256"),
                requiredText(frozen, "mcuPayloadSha256"),
                photos);
    }

    private static Measurement measurement(JsonNode node) {
        return new Measurement(
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

    private static CleanPhoto photo(JsonNode node) {
        return new CleanPhoto(
                requiredText(node, "slot"),
                requiredText(node, "status"),
                nullableUuid(node, "photoUid"),
                nullableText(node, "url"),
                nullableText(node, "sha256"),
                nullableLong(node, "sizeBytes"),
                nullableInstant(node, "capturedAt"),
                nullableText(node, "missingReason"));
    }

    private static void requireNormalCompletion(CleanFact fact) {
        Measurement pre = fact.preUnlockMeasurement();
        Measurement finalMeasurement = fact.finalMeasurement();
        boolean stableFinal = "STABLE".equals(
                finalMeasurement.status());
        if (!"STABLE".equals(pre.status())
                || !pre.weightValueAvailable()
                || !"STABLE_WINDOW_MEAN".equals(
                pre.weightValueKind())
                || pre.reportedWeightGrams() == null
                || pre.sampleCount() < 1
                || !"OK".equals(pre.sensorHealth())
                || pre.faultCode() != null
                || !fact.cleanerCompletionConfirmed()
                || !fact.cleanerPhysicalCloseConfirmed()
                || !"DEENERGIZED".equals(fact.lockPowerState())
                || !Set.of("OK", "UNKNOWN").contains(
                fact.solenoidHealth())
                || !"CLEANER_CONFIRMATION".equals(
                fact.physicalDoorStateBasis())
                || fact.actionSequence() < 1
                || fact.actionSequence() > 65_535
                || fact.photos().size() != 4
                || !fact.photos().stream()
                .map(CleanPhoto::slot)
                .collect(Collectors.toSet())
                .equals(PHOTO_POSITIONS)
                || (stableFinal
                && (!finalMeasurement.weightValueAvailable()
                || !"STABLE_WINDOW_MEAN".equals(
                finalMeasurement.weightValueKind())
                || finalMeasurement.reportedWeightGrams() == null
                || finalMeasurement.sampleCount() < 1
                || !"OK".equals(finalMeasurement.sensorHealth())
                || finalMeasurement.faultCode() != null
                || !finalMeasurement.reportedWeightGrams().equals(
                fact.newBaselineWeightGrams())))
                || (!stableFinal
                && (fact.newBaselineWeightGrams() != null
                || finalMeasurement.faultCode() == null))) {
            throw new IllegalArgumentException(
                    "clean completion is not a supported normal result");
        }
    }

    private static Calculation calculate(
            CleanFact fact,
            Operation operation) {
        String deviceStatus = fact.removedNetWeightGrams() == null
                ? "FAILED" : "RELIABLE";
        String recalculatedStatus;
        Long recalculated;
        if ("TRUSTED".equals(operation.oldBaselineState())
                && operation.oldBaselineWeightGrams() != null) {
            recalculated = Math.subtractExact(
                    fact.preUnlockMeasurement()
                            .reportedWeightGrams(),
                    operation.oldBaselineWeightGrams());
            recalculatedStatus = recalculated >= 0
                    ? "RELIABLE" : "INVALID";
        } else {
            recalculated = null;
            recalculatedStatus = "UNAVAILABLE";
        }
        String finalStatus;
        Long finalWeight;
        if ("STABLE".equals(fact.finalMeasurement().status())) {
            finalWeight = fact.finalMeasurement()
                    .reportedWeightGrams();
            finalStatus = finalWeight >= 0
                    ? "RELIABLE" : "INVALID";
        } else {
            finalWeight = null;
            finalStatus = "FAILED";
        }
        boolean reportedMatches = recalculated != null
                && fact.removedNetWeightGrams() != null
                && recalculated.equals(
                fact.removedNetWeightGrams());
        String recordClass = "RELIABLE".equals(
                recalculatedStatus)
                && "RELIABLE".equals(finalStatus)
                && reportedMatches
                ? "NORMAL" : "SYSTEM_ANOMALY";
        return new Calculation(
                deviceStatus,
                recalculatedStatus,
                recalculated,
                finalStatus,
                finalWeight,
                recordClass);
    }

    private static byte[] fullnessRuleFingerprint(
            Operation operation) {
        String value = String.join(
                "|",
                "FULLNESS_RULE_V1",
                operation.fullnessMode(),
                Long.toString(
                        operation.configuredFullWeightGrams()),
                Long.toString(operation.fullnessSettleWaitMs()),
                Long.toString(
                        operation.fullnessConfirmationWaitMs()),
                Long.toString(operation.measurementTimeoutMs()));
        return sha256(value);
    }

    private LocalDateTime databaseNow() {
        return jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)",
                LocalDateTime.class);
    }

    private long requiredId(
            String sql,
            Object argument,
            String subject) {
        Long id = jdbc.queryForObject(sql, Long.class, argument);
        if (id == null) {
            throw new IllegalStateException(subject + " id is missing");
        }
        return id;
    }

    private static String cleanRecordNo(UUID operationUid) {
        return "CR-" + operationUid;
    }

    private static JsonNode requiredObject(
            JsonNode parent,
            String field) {
        JsonNode value = parent.get(field);
        if (value == null || !value.isObject()) {
            throw new IllegalArgumentException(
                    field + " must be an object");
        }
        return value;
    }

    private static JsonNode requiredArray(
            JsonNode parent,
            String field) {
        JsonNode value = parent.get(field);
        if (value == null || !value.isArray()) {
            throw new IllegalArgumentException(
                    field + " must be an array");
        }
        return value;
    }

    private static String requiredText(
            JsonNode parent,
            String field) {
        JsonNode value = parent.get(field);
        if (value == null || !value.isTextual()
                || value.textValue().isBlank()) {
            throw new IllegalArgumentException(
                    field + " must be non-blank text");
        }
        return value.textValue();
    }

    private static String nullableText(
            JsonNode parent,
            String field) {
        JsonNode value = parent.get(field);
        if (value == null || value.isNull()) {
            return null;
        }
        if (!value.isTextual() || value.textValue().isBlank()) {
            throw new IllegalArgumentException(
                    field + " must be null or non-blank text");
        }
        return value.textValue();
    }

    private static boolean requiredBoolean(
            JsonNode parent,
            String field) {
        JsonNode value = parent.get(field);
        if (value == null || !value.isBoolean()) {
            throw new IllegalArgumentException(
                    field + " must be boolean");
        }
        return value.booleanValue();
    }

    private static UUID uuid(JsonNode parent, String field) {
        return UUID.fromString(requiredText(parent, field));
    }

    private static UUID nullableUuid(
            JsonNode parent,
            String field) {
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

    private static Long nullableLong(
            JsonNode parent,
            String field) {
        JsonNode value = parent.get(field);
        if (value == null || value.isNull()) {
            return null;
        }
        try {
            return value.decimalValue().longValueExact();
        } catch (RuntimeException exception) {
            throw new IllegalArgumentException(
                    field + " must be null or an integer",
                    exception);
        }
    }

    private static Long nullableLong(
            ResultSet rs,
            String column) throws SQLException {
        long value = rs.getLong(column);
        return rs.wasNull() ? null : value;
    }

    private static Instant nullableInstant(
            JsonNode parent,
            String field) {
        String value = nullableText(parent, field);
        return value == null ? null : Instant.parse(value);
    }

    private static LocalDateTime utc(Instant instant) {
        return instant == null
                ? null
                : LocalDateTime.ofInstant(
                instant,
                ZoneOffset.UTC);
    }

    private static byte[] nullableDigest(String value) {
        return value == null ? null : digest(value);
    }

    private static byte[] digest(String value) {
        try {
            byte[] bytes = HexFormat.of().parseHex(value);
            if (bytes.length != 32) {
                throw new IllegalArgumentException(
                        "digest must contain 32 bytes");
            }
            return bytes;
        } catch (IllegalArgumentException exception) {
            throw new IllegalArgumentException(
                    "digest must be lowercase SHA-256 hex",
                    exception);
        }
    }

    private static byte[] sha256(String value) {
        try {
            return MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException(exception);
        }
    }

    private static void requireSingle(
            int affected,
            String action) {
        if (affected != 1) {
            throw new IllegalStateException(
                    action + " affected " + affected + " rows");
        }
    }

    private static void requireSingleOrInserted(
            int affected,
            String action) {
        if (affected != 0 && affected != 1) {
            throw new IllegalStateException(
                    action + " affected " + affected + " rows");
        }
    }

    private static UntrustedInboxSourceException untrusted(
            String message) {
        return new UntrustedInboxSourceException(message);
    }

    private static Operation operation(ResultSet rs)
            throws SQLException {
        return new Operation(
                rs.getLong("id"),
                UUID.fromString(rs.getString("operation_uid")),
                rs.getLong("tenant_id"),
                rs.getLong("organization_id"),
                rs.getLong("deployment_id"),
                rs.getLong("port_id"),
                rs.getInt("port_no"),
                rs.getLong("cleaner_organization_user_id"),
                rs.getLong("device_config_version_id"),
                rs.getLong("config_version_no"),
                rs.getBytes("config_content_sha256"),
                rs.getBytes("config_mcu_sha256"),
                rs.getLong("port_config_snapshot_id"),
                rs.getString("fullness_mode"),
                rs.getLong("configured_full_weight_g"),
                rs.getLong("fullness_settle_wait_ms"),
                rs.getLong("fullness_confirmation_wait_ms"),
                rs.getLong("weight_measurement_timeout_ms"),
                rs.getLong("weight_minimum_g"),
                rs.getLong("weight_maximum_g"),
                rs.getLong("calibration_version"),
                rs.getLong("clean_config_version_id"),
                rs.getLong("clean_config_version_no"),
                rs.getString("old_bag_binding_state"),
                nullableLong(rs, "old_bag_id"),
                rs.getString("old_bag_code_snapshot"),
                nullableUuid(rs.getString("old_bag_uid")),
                rs.getString("old_baseline_state"),
                nullableLong(rs, "old_baseline_id"),
                nullableLong(rs, "old_baseline_weight_g"),
                rs.getLong("new_bag_id"),
                rs.getString("new_bag_code_snapshot"),
                UUID.fromString(rs.getString("new_bag_uid")),
                nullableLong(
                        rs,
                        "pending_delivery_result_session_id"),
                rs.getString("status"),
                nullableLong(rs, "completion_record_id"));
    }

    private static UUID nullableUuid(String value) {
        return value == null ? null : UUID.fromString(value);
    }

    private record Asset(long id) {
    }

    private record Deployment(long id, String publicCode) {
    }

    private record Command(
            long id,
            UUID uid,
            String physicalState) {
    }

    private record Capacity(
            Long currentDetectionId,
            byte[] ruleFingerprint,
            long lockVersion) {

        private Capacity {
            ruleFingerprint = ruleFingerprint == null
                    ? null : ruleFingerprint.clone();
        }

        @Override
        public byte[] ruleFingerprint() {
            return ruleFingerprint == null
                    ? null : ruleFingerprint.clone();
        }
    }

    private record BagSwap(long installedEventId) {
    }

    private record Baseline(
            Long id,
            Long weightGrams,
            String state) {
    }

    private record Detection(
            long id,
            UUID uid,
            boolean samplingRequired) {
    }

    private record Calculation(
            String deviceStatus,
            String recalculatedStatus,
            Long recalculatedWeightGrams,
            String finalStatus,
            Long finalWeightGrams,
            String recordClass) {
    }

    private record ExistingEdge(
            String eventUid,
            long deploymentId,
            long sequence,
            String canonicalSha256,
            long inboxId) {

        private boolean matches(
                CleanFact fact,
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

    private record Operation(
            long id,
            UUID uid,
            long tenantId,
            long organizationId,
            long deploymentId,
            long portId,
            int portNo,
            long cleanerOrganizationUserId,
            long deviceConfigVersionId,
            long configVersionNo,
            byte[] configContentSha256,
            byte[] configMcuSha256,
            long portConfigSnapshotId,
            String fullnessMode,
            long configuredFullWeightGrams,
            long fullnessSettleWaitMs,
            long fullnessConfirmationWaitMs,
            long measurementTimeoutMs,
            long weightMinimumGrams,
            long weightMaximumGrams,
            long calibrationVersion,
            long cleanConfigVersionId,
            long cleanConfigVersionNo,
            String oldBagBindingState,
            Long oldBagId,
            String oldBagCode,
            UUID oldBagUid,
            String oldBaselineState,
            Long oldBaselineId,
            Long oldBaselineWeightGrams,
            long newBagId,
            String newBagCode,
            UUID newBagUid,
            Long pendingDeliverySessionId,
            String status,
            Long completionRecordId) {

        private Operation {
            configContentSha256 = configContentSha256.clone();
            configMcuSha256 = configMcuSha256.clone();
        }

        @Override
        public byte[] configContentSha256() {
            return configContentSha256.clone();
        }

        @Override
        public byte[] configMcuSha256() {
            return configMcuSha256.clone();
        }
    }

    private record Measurement(
            UUID measurementUid,
            String status,
            boolean weightValueAvailable,
            Long reportedWeightGrams,
            String weightValueKind,
            long measurementElapsedMs,
            int sampleCount,
            long calibrationVersion,
            String sensorHealth,
            String faultCode,
            long mcuBootId,
            long mcuEventSequence) {
    }

    private record CleanPhoto(
            String slot,
            String status,
            UUID photoUid,
            String url,
            String sha256,
            Long sizeBytes,
            Instant capturedAt,
            String missingReason) {
    }

    private record PhotoSlot(
            long id,
            String status) {

        private boolean terminal() {
            return isTerminalPhotoState(status);
        }
    }

    private record CleanFact(
            UUID eventUid,
            UUID commandUid,
            UUID operationUid,
            String hardwareSn,
            String deploymentCode,
            long edgeEventSequence,
            Instant deviceOccurredAt,
            String clockQuality,
            String payloadSha256,
            String canonicalSha256,
            int portNo,
            UUID oldBagUid,
            UUID newBagUid,
            Measurement preUnlockMeasurement,
            Measurement finalMeasurement,
            Long removedNetWeightGrams,
            Long newBaselineWeightGrams,
            boolean cleanerCompletionConfirmed,
            int actionSequence,
            String lockPowerState,
            String solenoidHealth,
            String physicalDoorStateBasis,
            boolean cleanerPhysicalCloseConfirmed,
            long configurationVersion,
            String configurationContentSha256,
            String configurationMcuPayloadSha256,
            List<CleanPhoto> photos) {

        private CleanFact {
            photos = List.copyOf(photos);
        }
    }
}
