package org.enveloping.ecobin.recycling.application.fullness;

import org.enveloping.ecobin.device.api.command.ScheduleFullnessSampleCommand;
import org.enveloping.ecobin.device.api.port.CompleteFullnessSampleDeviceParticipationPort;
import org.enveloping.ecobin.device.api.port.ScheduleFullnessSampleDevicePort;
import org.enveloping.ecobin.device.api.result.DeliveryCompletionResultReference;
import org.enveloping.ecobin.device.api.result.FullnessSampleBusinessResult;
import org.enveloping.ecobin.device.api.result.FullnessSamplePersistenceFacts;
import org.enveloping.ecobin.device.api.result.FullnessSamplePhysicalFact;
import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;
import org.enveloping.ecobin.framework.reliability.UntrustedInboxSourceException;
import org.enveloping.ecobin.recycling.api.port.ApplyFullnessSampleCompleteUseCase;
import org.enveloping.ecobin.recycling.infrastructure.fullness.TransactionBoundFullnessDetectionCommandRef;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.Arrays;
import java.util.List;
import java.util.UUID;

@Service
public class ApplyFullnessSampleCompleteService
        implements ApplyFullnessSampleCompleteUseCase {

    private final CompleteFullnessSampleDeviceParticipationPort
            deviceCompletion;
    private final ScheduleFullnessSampleDevicePort sampleScheduler;
    private final JdbcTemplate jdbc;

    public ApplyFullnessSampleCompleteService(
            CompleteFullnessSampleDeviceParticipationPort
                    deviceCompletion,
            ScheduleFullnessSampleDevicePort sampleScheduler,
            JdbcTemplate jdbc) {
        this.deviceCompletion = deviceCompletion;
        this.sampleScheduler = sampleScheduler;
        this.jdbc = jdbc;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public TrustedDeviceEventApplyResult apply(
            TrustedDeviceInboxEvent event) {
        return deviceCompletion.complete(
                event,
                reference -> reference.useOnce(
                        this::persistBusinessFacts));
    }

    private FullnessSampleBusinessResult persistBusinessFacts(
            FullnessSamplePersistenceFacts facts) {
        DetectionRow detection = lockDetection(facts);
        CapacityRow capacity = lockCapacity(facts);
        requireCurrentBag(facts, detection);
        verifyEvent(facts.physicalFact(), detection);

        SampleCalculation calculation =
                calculate(facts, detection);
        long sampleId = insertSample(
                facts,
                detection,
                calculation);
        boolean currentGeneration =
                currentGeneration(
                        detection,
                        capacity,
                        facts);

        if ("INITIAL".equals(
                facts.physicalFact().sampleRole())
                && "FULL".equals(calculation.conclusion())
                && currentGeneration) {
            moveToConfirmation(
                    facts,
                    detection,
                    capacity,
                    sampleId,
                    calculation);
            scheduleConfirmation(facts, detection);
        } else {
            finishDetection(
                    facts,
                    detection,
                    capacity,
                    sampleId,
                    calculation,
                    currentGeneration);
        }

        return new FullnessSampleBusinessResult(
                List.of(
                        new DeliveryCompletionResultReference(
                                "FULLNESS_DETECTION",
                                detection.detectionUid().toString())));
    }

    private DetectionRow lockDetection(
            FullnessSamplePersistenceFacts facts) {
        List<DetectionRow> rows = jdbc.query("""
                        SELECT detection_uid,
                               tenant_id, organization_id,
                               deployment_id, port_id,
                               trigger_type,
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
                               status,
                               initial_sample_id,
                               initial_sample_conclusion
                        FROM rec_fullness_detection
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND port_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> detection(rs),
                facts.detectionId(),
                facts.tenantId(),
                facts.organizationId(),
                facts.deploymentId(),
                facts.portId());
        if (rows.size() != 1) {
            throw untrusted(
                    "fullness detection is missing");
        }
        DetectionRow row = rows.getFirst();
        if (!List.of(
                "PENDING_INITIAL_SAMPLE",
                "WAITING_RECHECK").contains(row.status())) {
            throw untrusted(
                    "fullness detection is already terminal");
        }
        return row;
    }

    private CapacityRow lockCapacity(
            FullnessSamplePersistenceFacts facts) {
        List<CapacityRow> rows = jdbc.query("""
                        SELECT baseline_state,
                               current_baseline_id,
                               current_baseline_weight_g,
                               detection_gate,
                               current_detection_id,
                               current_rule_fingerprint,
                               current_fullness_event_id
                        FROM rec_port_capacity_state
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND port_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> capacity(rs),
                facts.tenantId(),
                facts.organizationId(),
                facts.deploymentId(),
                facts.portId());
        if (rows.size() != 1) {
            throw untrusted(
                    "fullness capacity state is missing");
        }
        return rows.getFirst();
    }

    private void requireCurrentBag(
            FullnessSamplePersistenceFacts facts,
            DetectionRow detection) {
        List<Long> rows = jdbc.query("""
                        SELECT bag_id
                        FROM rec_bag_current_occupancy
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND port_id = ?
                          AND occupancy_type = 'PORT_BOUND'
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("bag_id"),
                facts.tenantId(),
                facts.organizationId(),
                facts.portId());
        if (rows.size() > 1) {
            throw new IllegalStateException(
                    "multiple bags occupy one fullness port");
        }
        /*
         * A missing or changed bag is a stale generation, not an untrusted
         * device event. The exact comparison happens in currentGeneration.
         */
    }

    private static void verifyEvent(
            FullnessSamplePhysicalFact fact,
            DetectionRow detection) {
        String expectedRole =
                "PENDING_INITIAL_SAMPLE".equals(
                        detection.status())
                        ? "INITIAL"
                        : "CONFIRMATION";
        if (!fact.detectionUid().equals(
                detection.detectionUid())
                || !fact.triggerType().equals(
                detection.triggerType())
                || !fact.sampleRole().equals(expectedRole)
                || !fact.fullnessMode().equals(
                machineFullnessMode(
                        detection.decisionMode()))
                || !"FIXED_FRAME_TOTAL_WEIGHT".equals(
                detection.calculationBasis())) {
            throw untrusted(
                    "fullness result differs from its detection state");
        }
    }

    private static SampleCalculation calculate(
            FullnessSamplePersistenceFacts facts,
            DetectionRow detection) {
        long totalWeight = facts.physicalFact()
                .totalWeightMeasurement()
                .reportedWeightGrams();
        BigDecimal percent = BigDecimal.valueOf(totalWeight)
                .multiply(BigDecimal.valueOf(100))
                .divide(
                        BigDecimal.valueOf(
                                detection.configuredFullWeightGrams()),
                        2,
                        RoundingMode.HALF_UP);
        if (percent.signum() < 0) {
            percent = BigDecimal.ZERO.setScale(2);
        }
        boolean full =
                percent.compareTo(
                        BigDecimal.valueOf(100)) >= 0;
        Long rawNet = detection.baselineWeightGrams() == null
                ? null
                : Math.subtractExact(
                        totalWeight,
                        detection.baselineWeightGrams());
        return new SampleCalculation(
                totalWeight,
                rawNet,
                percent,
                full ? "FULL" : "NOT_FULL",
                full ? "WEIGHT" : null);
    }

    private long insertSample(
            FullnessSamplePersistenceFacts facts,
            DetectionRow detection,
            SampleCalculation calculation) {
        boolean infraredFull = "BLOCKED".equals(
                facts.physicalFact().fullnessSensorValue());
        requireSingle(jdbc.update("""
                        INSERT INTO rec_fullness_sample (
                            tenant_id, organization_id,
                            detection_id, sample_role,
                            physical_result_id,
                            infrared_status,
                            infrared_value,
                            infrared_full,
                            fullness_sensor_kind,
                            fullness_sample_basis,
                            representative_distance_mm,
                            requested_sample_count,
                            valid_sample_count,
                            calculation_basis,
                            weight_status,
                            stable_total_weight_g,
                            baseline_weight_g,
                            threshold_weight_g,
                            raw_net_weight_g,
                            displayed_fullness_percent,
                            weight_full,
                            conclusion,
                            full_reason,
                            device_occurred_at,
                            backend_received_at,
                            created_at
                        ) VALUES (
                            ?, ?,
                            ?, ?,
                            ?,
                            'RELIABLE',
                            ?,
                            ?,
                            ?,
                            ?,
                            ?,
                            ?,
                            ?,
                            'FIXED_FRAME_TOTAL_WEIGHT',
                            'RELIABLE',
                            ?,
                            ?,
                            ?,
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
                facts.tenantId(),
                facts.organizationId(),
                facts.detectionId(),
                facts.physicalFact().sampleRole(),
                facts.physicalResultId(),
                facts.physicalFact().fullnessSensorValue(),
                infraredFull,
                facts.physicalFact().fullnessSensorKind(),
                facts.physicalFact().fullnessSampleBasis(),
                facts.physicalFact().representativeDistanceMm(),
                facts.physicalFact().requestedSampleCount(),
                facts.physicalFact().validSampleCount(),
                calculation.totalWeightGrams(),
                detection.baselineWeightGrams(),
                detection.configuredFullWeightGrams(),
                calculation.rawNetWeightGrams(),
                calculation.displayedPercent(),
                "FULL".equals(calculation.conclusion()),
                calculation.conclusion(),
                calculation.fullReason(),
                LocalDateTime.ofInstant(
                        facts.physicalFact().deviceOccurredAt(),
                        ZoneOffset.UTC),
                facts.backendReceivedAt(),
                facts.backendReceivedAt()),
                "insert fullness sample");
        Long id = jdbc.queryForObject("""
                        SELECT id
                        FROM rec_fullness_sample
                        WHERE physical_result_id = ?
                        """,
                Long.class,
                facts.physicalResultId());
        if (id == null) {
            throw new IllegalStateException(
                    "fullness sample id is missing");
        }
        return id;
    }

    private boolean currentGeneration(
            DetectionRow detection,
            CapacityRow capacity,
            FullnessSamplePersistenceFacts facts) {
        List<Long> bags = jdbc.query("""
                        SELECT bag_id
                        FROM rec_bag_current_occupancy
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND port_id = ?
                          AND occupancy_type = 'PORT_BOUND'
                        """,
                (rs, ignored) -> rs.getLong("bag_id"),
                facts.tenantId(),
                facts.organizationId(),
                facts.portId());
        boolean baselineMatches =
                switch (detection.baselineState()) {
                    case "VALID" ->
                            "VALID".equals(capacity.baselineState())
                                    && detection.baselineId() != null
                                    && detection.baselineId().equals(
                                    capacity.baselineId())
                                    && detection.baselineWeightGrams()
                                    .equals(
                                            capacity
                                                    .baselineWeightGrams());
                    case "INVALID", "MISSING" ->
                            !"VALID".equals(
                                    capacity.baselineState())
                                    && detection.baselineId() == null
                                    && detection.baselineWeightGrams()
                                    == null;
                    default -> false;
                };
        return bags.size() == 1
                && bags.getFirst() == detection.bagId()
                && capacity.currentDetectionId() != null
                && capacity.currentDetectionId()
                == facts.detectionId()
                && Arrays.equals(
                        capacity.ruleFingerprint(),
                        detection.ruleFingerprint())
                && baselineMatches;
    }

    private void moveToConfirmation(
            FullnessSamplePersistenceFacts facts,
            DetectionRow detection,
            CapacityRow capacity,
            long sampleId,
            SampleCalculation calculation) {
        LocalDateTime next = facts.backendReceivedAt()
                .plusNanos(
                        detection.confirmationWaitMs()
                                * 1_000_000L);
        requireSingle(jdbc.update("""
                        UPDATE rec_fullness_detection
                        SET status = 'WAITING_RECHECK',
                            initial_sample_id = ?,
                            initial_sample_conclusion = 'FULL',
                            next_sample_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND status =
                              'PENDING_INITIAL_SAMPLE'
                        """,
                sampleId,
                next,
                facts.backendReceivedAt(),
                facts.detectionId(),
                facts.tenantId(),
                facts.organizationId()),
                "advance fullness detection to confirmation");
        requireSingle(jdbc.update("""
                        UPDATE rec_port_capacity_state
                        SET latest_stable_total_weight_g = ?,
                            raw_net_weight_g = ?,
                            displayed_fullness_percent = ?,
                            detection_gate = 'IN_PROGRESS',
                            confirmed_fullness_state = 'UNKNOWN',
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND port_id = ?
                          AND current_detection_id = ?
                        """,
                calculation.totalWeightGrams(),
                calculation.rawNetWeightGrams(),
                calculation.displayedPercent(),
                facts.backendReceivedAt(),
                facts.tenantId(),
                facts.organizationId(),
                facts.deploymentId(),
                facts.portId(),
                facts.detectionId()),
                "mark fullness confirmation in progress");
    }

    private void scheduleConfirmation(
            FullnessSamplePersistenceFacts facts,
            DetectionRow detection) {
        sampleScheduler.schedule(
                new ScheduleFullnessSampleCommand(
                        TransactionBoundFullnessDetectionCommandRef.issue(
                                facts.tenantId(),
                                facts.organizationId(),
                                facts.deploymentId(),
                                facts.portId(),
                                facts.detectionId(),
                                detection.deviceConfigVersionId(),
                                detection.portConfigSnapshotId()),
                        detection.detectionUid(),
                        facts.physicalFact().portNo(),
                        "CONFIRMATION",
                        detection.triggerType(),
                        detection.decisionMode(),
                        detection.baselineWeightGrams(),
                        detection.configuredFullWeightGrams(),
                        detection.confirmationWaitMs(),
                        detection.measurementTimeoutMs(),
                        facts.physicalFact()
                                .configurationVersion(),
                        facts.physicalFact()
                                .configurationContentSha256(),
                        facts.physicalFact()
                                .configurationMcuPayloadSha256(),
                        detection.detectionUid(),
                        facts.physicalFact().eventUid()));
    }

    private void finishDetection(
            FullnessSamplePersistenceFacts facts,
            DetectionRow detection,
            CapacityRow capacity,
            long sampleId,
            SampleCalculation calculation,
            boolean currentGeneration) {
        boolean initial = "INITIAL".equals(
                facts.physicalFact().sampleRole());
        String disposition = currentGeneration
                ? "APPLIED"
                : "STALE_IGNORED";
        requireSingle(jdbc.update("""
                        UPDATE rec_fullness_detection
                        SET status = 'COMPLETED',
                            final_result = ?,
                            disposition = ?,
                            initial_sample_id =
                                CASE
                                    WHEN ? THEN ?
                                    ELSE initial_sample_id
                                END,
                            initial_sample_conclusion =
                                CASE
                                    WHEN ? THEN ?
                                    ELSE initial_sample_conclusion
                                END,
                            terminal_sample_id = ?,
                            terminal_sample_conclusion = ?,
                            next_sample_at = NULL,
                            completed_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND status = ?
                        """,
                calculation.conclusion(),
                disposition,
                initial,
                sampleId,
                initial,
                calculation.conclusion(),
                sampleId,
                calculation.conclusion(),
                facts.backendReceivedAt(),
                facts.backendReceivedAt(),
                facts.detectionId(),
                facts.tenantId(),
                facts.organizationId(),
                detection.status()),
                "complete fullness detection");
        if (!currentGeneration) {
            return;
        }
        if ("FULL".equals(calculation.conclusion())) {
            FullnessEvent event = applyFullEvent(
                    facts,
                    detection,
                    sampleId,
                    calculation,
                    initial);
            updateFinalCapacity(
                    facts,
                    calculation,
                    "FULL",
                    event.id());
        } else {
            recoverFullEvent(
                    facts,
                    capacity.currentFullnessEventId());
            updateFinalCapacity(
                    facts,
                    calculation,
                    "NOT_FULL",
                    null);
        }
    }

    private FullnessEvent applyFullEvent(
            FullnessSamplePersistenceFacts facts,
            DetectionRow detection,
            long sampleId,
            SampleCalculation calculation,
            boolean initial) {
        List<FullnessEvent> active = jdbc.query("""
                        SELECT id, event_uid, detection_count
                        FROM rec_fullness_event
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND port_id = ?
                          AND status = 'ACTIVE'
                        FOR UPDATE
                        """,
                (rs, ignored) -> new FullnessEvent(
                        rs.getLong("id"),
                        UUID.fromString(
                                rs.getString("event_uid")),
                        rs.getLong("detection_count")),
                facts.tenantId(),
                facts.organizationId(),
                facts.portId());
        if (!active.isEmpty()) {
            FullnessEvent event = active.getFirst();
            requireSingle(jdbc.update("""
                            UPDATE rec_fullness_event
                            SET current_reason = ?,
                                latest_detection_id = ?,
                                latest_full_detection_id = ?,
                                detection_count =
                                    detection_count + 1,
                                updated_at = ?
                            WHERE id = ?
                              AND status = 'ACTIVE'
                            """,
                    calculation.fullReason(),
                    facts.detectionId(),
                    facts.detectionId(),
                    facts.backendReceivedAt(),
                    event.id()),
                    "extend active fullness event");
            return event;
        }
        LocalDateTime firstDetectedAt =
                initial
                        ? facts.backendReceivedAt()
                        : jdbc.queryForObject("""
                                        SELECT device_occurred_at
                                        FROM rec_fullness_sample
                                        WHERE id = ?
                                        """,
                                LocalDateTime.class,
                                detection.initialSampleId());
        UUID eventUid = UUID.randomUUID();
        requireSingle(jdbc.update("""
                        INSERT INTO rec_fullness_event (
                            event_uid,
                            tenant_id, organization_id,
                            port_id,
                            status,
                            first_detected_detection_id,
                            first_detected_at,
                            confirmed_detection_id,
                            confirmed_at,
                            current_reason,
                            latest_detection_id,
                            latest_full_detection_id,
                            detection_count,
                            recovered_by_detection_id,
                            recovered_at,
                            updated_at
                        ) VALUES (
                            ?,
                            ?, ?,
                            ?,
                            'ACTIVE',
                            ?,
                            ?,
                            ?,
                            ?,
                            ?,
                            ?,
                            ?,
                            1,
                            NULL,
                            NULL,
                            ?
                        )
                        """,
                eventUid.toString(),
                facts.tenantId(),
                facts.organizationId(),
                facts.portId(),
                facts.detectionId(),
                firstDetectedAt,
                facts.detectionId(),
                facts.backendReceivedAt(),
                calculation.fullReason(),
                facts.detectionId(),
                facts.detectionId(),
                facts.backendReceivedAt()),
                "create active fullness event");
        Long id = jdbc.queryForObject("""
                        SELECT id
                        FROM rec_fullness_event
                        WHERE event_uid = ?
                        """,
                Long.class,
                eventUid.toString());
        if (id == null) {
            throw new IllegalStateException(
                    "fullness event id is missing");
        }
        return new FullnessEvent(id, eventUid, 1);
    }

    private void recoverFullEvent(
            FullnessSamplePersistenceFacts facts,
            Long currentEventId) {
        if (currentEventId == null) {
            return;
        }
        int updated = jdbc.update("""
                        UPDATE rec_fullness_event
                        SET status = 'RECOVERED',
                            latest_detection_id = ?,
                            recovered_by_detection_id = ?,
                            recovered_at = ?,
                            updated_at = ?
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND port_id = ?
                          AND status = 'ACTIVE'
                        """,
                facts.detectionId(),
                facts.detectionId(),
                facts.backendReceivedAt(),
                facts.backendReceivedAt(),
                currentEventId,
                facts.tenantId(),
                facts.organizationId(),
                facts.portId());
        if (updated != 0 && updated != 1) {
            throw new IllegalStateException(
                    "recover fullness event affected "
                            + updated
                            + " rows");
        }
    }

    private void updateFinalCapacity(
            FullnessSamplePersistenceFacts facts,
            SampleCalculation calculation,
            String fullnessState,
            Long fullnessEventId) {
        requireSingle(jdbc.update("""
                        UPDATE rec_port_capacity_state
                        SET latest_stable_total_weight_g = ?,
                            raw_net_weight_g = ?,
                            displayed_fullness_percent = ?,
                            detection_gate = 'READY',
                            current_detection_id = NULL,
                            confirmed_fullness_state = ?,
                            last_detection_id = ?,
                            current_fullness_event_id = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND port_id = ?
                          AND current_detection_id = ?
                          AND detection_gate IN (
                              'PENDING',
                              'IN_PROGRESS'
                          )
                        """,
                calculation.totalWeightGrams(),
                calculation.rawNetWeightGrams(),
                calculation.displayedPercent(),
                fullnessState,
                facts.detectionId(),
                fullnessEventId,
                facts.backendReceivedAt(),
                facts.tenantId(),
                facts.organizationId(),
                facts.deploymentId(),
                facts.portId(),
                facts.detectionId()),
                "apply final fullness capacity");
    }

    private static DetectionRow detection(ResultSet rs)
            throws SQLException {
        return new DetectionRow(
                UUID.fromString(rs.getString("detection_uid")),
                rs.getString("trigger_type"),
                rs.getLong("bag_id"),
                rs.getString("baseline_state_snapshot"),
                nullableLong(rs, "baseline_id_snapshot"),
                nullableLong(
                        rs,
                        "baseline_weight_g_snapshot"),
                rs.getLong("device_config_version_id"),
                rs.getLong("port_config_snapshot_id"),
                rs.getBytes("rule_fingerprint"),
                rs.getString("decision_mode"),
                rs.getLong("configured_full_weight_g"),
                rs.getLong("settle_wait_ms"),
                rs.getLong("confirmation_wait_ms"),
                rs.getLong("measurement_timeout_ms"),
                rs.getString("calculation_basis"),
                rs.getString("status"),
                nullableLong(rs, "initial_sample_id"),
                rs.getString("initial_sample_conclusion"));
    }

    private static CapacityRow capacity(ResultSet rs)
            throws SQLException {
        return new CapacityRow(
                rs.getString("baseline_state"),
                nullableLong(rs, "current_baseline_id"),
                nullableLong(
                        rs,
                        "current_baseline_weight_g"),
                rs.getString("detection_gate"),
                nullableLong(rs, "current_detection_id"),
                rs.getBytes("current_rule_fingerprint"),
                nullableLong(
                        rs,
                        "current_fullness_event_id"));
    }

    private static Long nullableLong(
            ResultSet rs,
            String field) throws SQLException {
        long value = rs.getLong(field);
        return rs.wasNull() ? null : value;
    }

    private static String machineFullnessMode(
            String value) {
        return switch (value) {
            case "INFRARED_ONLY" -> "SENSOR_ONLY";
            case "WEIGHT_ONLY" -> "WEIGHT_ONLY";
            case "INFRARED_OR_WEIGHT" ->
                    "SENSOR_OR_WEIGHT";
            default -> throw untrusted(
                    "fullness detection mode is unsupported");
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

    private static UntrustedInboxSourceException untrusted(
            String detail) {
        return new UntrustedInboxSourceException(detail);
    }

    private record DetectionRow(
            UUID detectionUid,
            String triggerType,
            long bagId,
            String baselineState,
            Long baselineId,
            Long baselineWeightGrams,
            long deviceConfigVersionId,
            long portConfigSnapshotId,
            byte[] ruleFingerprint,
            String decisionMode,
            long configuredFullWeightGrams,
            long settleWaitMs,
            long confirmationWaitMs,
            long measurementTimeoutMs,
            String calculationBasis,
            String status,
            Long initialSampleId,
            String initialSampleConclusion) {
    }

    private record CapacityRow(
            String baselineState,
            Long baselineId,
            Long baselineWeightGrams,
            String detectionGate,
            Long currentDetectionId,
            byte[] ruleFingerprint,
            Long currentFullnessEventId) {
    }

    private record SampleCalculation(
            long totalWeightGrams,
            Long rawNetWeightGrams,
            BigDecimal displayedPercent,
            String conclusion,
            String fullReason) {
    }

    private record FullnessEvent(
            long id,
            UUID uid,
            long detectionCount) {
    }
}
