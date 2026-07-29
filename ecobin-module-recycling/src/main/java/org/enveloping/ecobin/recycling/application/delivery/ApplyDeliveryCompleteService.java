package org.enveloping.ecobin.recycling.application.delivery;

import org.enveloping.ecobin.device.api.port.CompleteDeliveryDeviceParticipationPort;
import org.enveloping.ecobin.device.api.result.DeliveryCompleteMeasurement;
import org.enveloping.ecobin.device.api.result.DeliveryCompletePhoto;
import org.enveloping.ecobin.device.api.result.DeliveryCompletePhysicalFact;
import org.enveloping.ecobin.device.api.result.DeliveryCompletionBusinessResult;
import org.enveloping.ecobin.device.api.result.DeliveryCompletionPersistenceFacts;
import org.enveloping.ecobin.device.api.result.DeliveryCompletionResultReference;
import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;
import org.enveloping.ecobin.framework.reliability.UntrustedInboxSourceException;
import org.enveloping.ecobin.recycling.api.port.ApplyDeliveryCompleteUseCase;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.math.BigInteger;
import java.math.RoundingMode;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.function.Function;
import java.util.stream.Collectors;

/**
 * Builds the single pending recycling order and the next-delivery fullness
 * gate from a trusted, stable delivery result.
 *
 * <p>The current slice intentionally stops at a pending fullness detection.
 * It does not schedule a sample, retry a device command, or recover physical
 * state.</p>
 */
@Service
public class ApplyDeliveryCompleteService
        implements ApplyDeliveryCompleteUseCase {

    private static final Set<String> PHOTO_POSITIONS = Set.of(
            "BEFORE_INNER",
            "BEFORE_OUTER",
            "AFTER_INNER",
            "AFTER_OUTER");

    static final String LOCK_CURRENT_BAG_OCCUPANCY_SQL = """
            SELECT bag_id
            FROM rec_bag_current_occupancy
            WHERE tenant_id = ?
              AND organization_id = ?
              AND bag_id = ?
              AND occupancy_type = 'PORT_BOUND'
              AND port_id = ?
            FOR UPDATE
            """;

    static final String LOAD_FROZEN_BAG_SQL = """
            SELECT id
            FROM rec_bag
            WHERE tenant_id = ?
              AND organization_id = ?
              AND id = ?
              AND bag_uid = ?
              AND bag_code = ?
            """;

    private final CompleteDeliveryDeviceParticipationPort deviceCompletion;
    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;

    public ApplyDeliveryCompleteService(
            CompleteDeliveryDeviceParticipationPort deviceCompletion,
            JdbcTemplate jdbc,
            ObjectMapper objectMapper) {
        this.deviceCompletion = deviceCompletion;
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
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

    private DeliveryCompletionBusinessResult persistBusinessFacts(
            DeliveryCompletionPersistenceFacts facts) {
        DeliveryConfiguration configuration =
                requireFrozenDeliveryConfiguration(facts);
        requireCurrentBag(facts);
        CapacityState capacity = lockCapacity(facts);

        long visibilitySequence = nextVisibilitySequence(facts);
        String orderNo = deliveryOrderNo(
                facts.physicalFact().sessionUid());
        OrderCalculation calculation = calculate(facts);
        long orderId = insertOrder(
                facts,
                configuration,
                visibilitySequence,
                orderNo,
                calculation);

        insertAnomalies(facts, orderId, calculation);
        insertPhotos(facts, orderId);
        DetectionResult detection = createPendingDetection(
                facts,
                orderId,
                capacity);

        return new DeliveryCompletionBusinessResult(
                orderNo,
                List.of(
                        new DeliveryCompletionResultReference(
                                "DELIVERY_ORDER",
                                orderNo),
                        new DeliveryCompletionResultReference(
                                "FULLNESS_DETECTION",
                                detection.uid().toString())));
    }

    private DeliveryConfiguration requireFrozenDeliveryConfiguration(
            DeliveryCompletionPersistenceFacts facts) {
        List<DeliveryConfiguration> rows = jdbc.query("""
                        SELECT version_no,
                               content_sha256,
                               open_balance_floor_cent,
                               max_review_abs_weight_g
                        FROM rec_organization_delivery_config
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND id = ?
                        """,
                (rs, ignored) -> new DeliveryConfiguration(
                        rs.getLong("version_no"),
                        rs.getBytes("content_sha256"),
                        rs.getLong("open_balance_floor_cent"),
                        rs.getLong("max_review_abs_weight_g")),
                facts.tenantId(),
                facts.organizationId(),
                facts.deliveryConfigVersionId());
        if (rows.size() != 1) {
            throw untrusted(
                    "frozen delivery configuration is missing");
        }
        DeliveryConfiguration configuration = rows.getFirst();
        if (!java.util.Arrays.equals(
                configuration.contentSha256(),
                facts.deliveryConfigContentSha256())
                || configuration.openBalanceFloorCent()
                != facts.openBalanceFloorCent()
                || configuration.maxReviewAbsWeightGrams()
                != facts.maxReviewAbsWeightGrams()) {
            throw untrusted(
                    "frozen delivery configuration differs");
        }
        return configuration;
    }

    private void requireCurrentBag(
            DeliveryCompletionPersistenceFacts facts) {
        List<Long> rows = jdbc.query(
                LOCK_CURRENT_BAG_OCCUPANCY_SQL,
                (rs, ignored) -> rs.getLong("bag_id"),
                facts.tenantId(),
                facts.organizationId(),
                facts.bagId(),
                facts.portId());
        if (rows.size() != 1) {
            throw untrusted(
                    "delivery bag is no longer bound to the port");
        }
        List<Long> bags = jdbc.query(
                LOAD_FROZEN_BAG_SQL,
                (rs, ignored) -> rs.getLong("id"),
                facts.tenantId(),
                facts.organizationId(),
                facts.bagId(),
                facts.bagUid().toString(),
                facts.bagCode());
        if (bags.size() != 1) {
            throw untrusted(
                    "delivery frozen bag identity differs");
        }
    }

    private CapacityState lockCapacity(
            DeliveryCompletionPersistenceFacts facts) {
        List<CapacityState> rows = jdbc.query("""
                        SELECT baseline_state,
                               current_baseline_id,
                               current_baseline_weight_g,
                               detection_gate,
                               current_detection_id,
                               current_rule_fingerprint,
                               confirmed_fullness_state
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
                    "delivery capacity state is missing");
        }
        CapacityState capacity = rows.getFirst();
        if (!"READY".equals(capacity.detectionGate())
                || capacity.currentDetectionId() != null
                || capacity.ruleFingerprint() == null
                || !"NOT_FULL".equals(
                        capacity.confirmedFullnessState())) {
            throw untrusted(
                    "delivery capacity generation has changed");
        }
        boolean weightParticipates =
                !"INFRARED_ONLY".equals(facts.fullnessMode());
        if (weightParticipates
                && (!"VALID".equals(capacity.baselineState())
                || capacity.baselineId() == null
                || capacity.baselineWeightGrams() == null)) {
            throw untrusted(
                    "delivery weight baseline has changed");
        }
        return capacity;
    }

    private long nextVisibilitySequence(
            DeliveryCompletionPersistenceFacts facts) {
        List<Long> rows = jdbc.query("""
                        SELECT last_visibility_sequence_no
                        FROM rec_organization_order_counter
                        WHERE tenant_id = ?
                          AND organization_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong(
                        "last_visibility_sequence_no"),
                facts.tenantId(),
                facts.organizationId());
        if (rows.size() != 1
                || rows.getFirst() >= 9_007_199_254_740_991L) {
            throw new IllegalStateException(
                    "organization order counter is unavailable");
        }
        long next = rows.getFirst() + 1;
        requireSingle(jdbc.update("""
                        UPDATE rec_organization_order_counter
                        SET last_visibility_sequence_no = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND last_visibility_sequence_no = ?
                        """,
                next,
                facts.backendReceivedAt(),
                facts.tenantId(),
                facts.organizationId(),
                rows.getFirst()),
                "advance organization delivery order counter");
        return next;
    }

    private long insertOrder(
            DeliveryCompletionPersistenceFacts facts,
            DeliveryConfiguration configuration,
            long visibilitySequence,
            String orderNo,
            OrderCalculation calculation) {
        DeliveryCompletePhysicalFact physical =
                facts.physicalFact();
        DeliveryCompleteMeasurement before =
                physical.firstPreOpenMeasurement();
        DeliveryCompleteMeasurement after =
                physical.finalPostCloseMeasurement();
        LocalDateTime occurredAt = utc(physical.deviceOccurredAt());
        requireSingle(jdbc.update("""
                        INSERT INTO rec_delivery_order (
                            delivery_order_no,
                            tenant_id, organization_id,
                            visibility_sequence_no,
                            delivery_session_id, physical_result_id,
                            organization_user_id,
                            deployment_id, port_id,
                            device_config_version_id,
                            delivery_config_version_id,
                            delivery_config_version_no,
                            delivery_config_content_sha256,
                            unit_price_yuan_per_kg,
                            open_balance_floor_cent,
                            bag_id, bag_uid_snapshot,
                            bag_code_snapshot,
                            negative_weight_anomaly_threshold_g,
                            max_review_abs_weight_g,
                            initial_weight_status, initial_weight_g,
                            final_weight_status, final_weight_g,
                            raw_net_weight_g,
                            negative_weight_anomaly,
                            raw_business_weight_kg,
                            raw_amount_cent,
                            raw_calculation_status,
                            review_status,
                            current_revision_no,
                            current_revision_id,
                            final_business_weight_kg,
                            final_amount_cent,
                            first_approved_at,
                            device_occurred_at,
                            backend_received_at,
                            created_at, updated_at
                        ) VALUES (
                            ?,
                            ?, ?,
                            ?,
                            ?, ?,
                            ?,
                            ?, ?,
                            ?,
                            ?,
                            ?,
                            ?,
                            ?,
                            ?,
                            ?, ?,
                            ?,
                            ?,
                            ?,
                            'RELIABLE', ?,
                            'RELIABLE', ?,
                            ?,
                            ?,
                            ?,
                            ?,
                            ?,
                            'PENDING',
                            0,
                            NULL,
                            NULL,
                            NULL,
                            NULL,
                            ?,
                            ?,
                            ?, ?
                        )
                        """,
                orderNo,
                facts.tenantId(),
                facts.organizationId(),
                visibilitySequence,
                facts.deliverySessionId(),
                facts.physicalResultId(),
                facts.organizationUserId(),
                facts.deploymentId(),
                facts.portId(),
                facts.deviceConfigVersionId(),
                facts.deliveryConfigVersionId(),
                configuration.versionNo(),
                facts.deliveryConfigContentSha256(),
                facts.unitPriceYuanPerKg(),
                facts.openBalanceFloorCent(),
                facts.bagId(),
                facts.bagUid().toString(),
                facts.bagCode(),
                facts.negativeWeightThresholdGrams(),
                facts.maxReviewAbsWeightGrams(),
                before.reportedWeightGrams(),
                after.reportedWeightGrams(),
                calculation.netWeightGrams(),
                physical.negativeWeightAnomaly(),
                calculation.businessWeightKg(),
                calculation.amountCent(),
                calculation.status(),
                occurredAt,
                facts.backendReceivedAt(),
                facts.backendReceivedAt(),
                facts.backendReceivedAt()),
                "insert pending delivery order");
        Long id = jdbc.queryForObject("""
                        SELECT id
                        FROM rec_delivery_order
                        WHERE delivery_order_no = ?
                        """,
                Long.class,
                orderNo);
        if (id == null) {
            throw new IllegalStateException(
                    "delivery order id is missing");
        }
        return id;
    }

    private void insertAnomalies(
            DeliveryCompletionPersistenceFacts facts,
            long orderId,
            OrderCalculation calculation) {
        DeliveryCompletePhysicalFact physical =
                facts.physicalFact();
        if (!Long.valueOf(calculation.netWeightGrams()).equals(
                physical.deliveryNetWeightGrams())) {
            insertAnomaly(
                    facts,
                    orderId,
                    "SYSTEM",
                    "DELIVERY_NET_WEIGHT_MISMATCH",
                    Map.of(
                            "reportedNetWeightGrams",
                            physical.deliveryNetWeightGrams(),
                            "calculatedNetWeightGrams",
                            calculation.netWeightGrams()));
        }
        if ("INVALID".equals(calculation.status())) {
            insertAnomaly(
                    facts,
                    orderId,
                    "SYSTEM",
                    "RAW_WEIGHT_OUT_OF_RANGE",
                    Map.of(
                            "calculatedNetWeightGrams",
                            calculation.netWeightGrams(),
                            "maxReviewAbsWeightGrams",
                            facts.maxReviewAbsWeightGrams()));
        }
        if (physical.negativeWeightAnomaly()) {
            insertAnomaly(
                    facts,
                    orderId,
                    "USER",
                    "NEGATIVE_WEIGHT_ANOMALY",
                    null);
        }
    }

    private void insertAnomaly(
            DeliveryCompletionPersistenceFacts facts,
            long orderId,
            String category,
            String code,
            Map<String, Object> diagnostic) {
        String json = diagnostic == null
                ? null
                : objectMapper.writeValueAsString(diagnostic);
        requireSingle(jdbc.update("""
                        INSERT INTO rec_delivery_anomaly (
                            tenant_id, organization_id,
                            delivery_order_id,
                            source_physical_result_id,
                            category, anomaly_code,
                            diagnostic_json,
                            detected_at, created_at
                        ) VALUES (
                            ?, ?,
                            ?,
                            ?,
                            ?, ?,
                            ?,
                            ?, ?
                        )
                        """,
                facts.tenantId(),
                facts.organizationId(),
                orderId,
                facts.physicalResultId(),
                category,
                code,
                json,
                facts.backendReceivedAt(),
                facts.backendReceivedAt()),
                "insert delivery anomaly " + code);
    }

    private void insertPhotos(
            DeliveryCompletionPersistenceFacts facts,
            long orderId) {
        Map<String, DeliveryCompletePhoto> photos =
                facts.physicalFact().photos().stream()
                        .collect(Collectors.toMap(
                                DeliveryCompletePhoto::slot,
                                Function.identity()));
        if (!photos.keySet().equals(PHOTO_POSITIONS)) {
            throw untrusted(
                    "delivery photo slots are incomplete");
        }
        for (String position : PHOTO_POSITIONS) {
            DeliveryCompletePhoto photo = photos.get(position);
            LocalDateTime linkedAt =
                    "UPLOAD_PENDING".equals(photo.status())
                            ? null
                            : facts.backendReceivedAt();
            requireSingle(jdbc.update("""
                            INSERT INTO rec_delivery_photo (
                                tenant_id, organization_id,
                                delivery_order_id, position,
                                photo_uid, status, object_url,
                                sha256, size_bytes, captured_at,
                                linked_at, missing_reason,
                                created_at, updated_at
                            ) VALUES (
                                ?, ?,
                                ?, ?,
                                ?, ?, ?,
                                ?, ?, ?,
                                ?, ?,
                                ?, ?
                            )
                            """,
                    facts.tenantId(),
                    facts.organizationId(),
                    orderId,
                    position,
                    photo.photoUid() == null
                            ? null
                            : photo.photoUid().toString(),
                    photo.status(),
                    photo.url(),
                    nullableDigest(photo.sha256()),
                    photo.sizeBytes(),
                    utc(photo.capturedAt()),
                    linkedAt,
                    photo.missingReason(),
                    facts.backendReceivedAt(),
                    facts.backendReceivedAt()),
                    "insert delivery photo " + position);
        }
    }

    private DetectionResult createPendingDetection(
            DeliveryCompletionPersistenceFacts facts,
            long orderId,
            CapacityState capacity) {
        String baselineSnapshot =
                switch (capacity.baselineState()) {
                    case "VALID" -> "VALID";
                    case "INVALID" -> "INVALID";
                    case "UNINITIALIZED" -> "MISSING";
                    default -> throw untrusted(
                            "unknown delivery baseline state");
                };
        UUID detectionUid = UUID.randomUUID();
        LocalDateTime nextSampleAt =
                facts.backendReceivedAt().plus(
                        Duration.ofMillis(
                                facts.fullnessSettleWaitMs()));
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
                            status, final_result, failure_code,
                            disposition,
                            initial_sample_id,
                            initial_sample_conclusion,
                            terminal_sample_id,
                            terminal_sample_conclusion,
                            next_sample_at, completed_at,
                            lock_version,
                            created_at, updated_at
                        ) VALUES (
                            ?,
                            ?, ?,
                            ?, ?,
                            'DELIVERY_COMPLETE',
                            ?, NULL,
                            NULL,
                            NULL, NULL,
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
                            ?,
                            'PENDING_INITIAL_SAMPLE',
                            NULL, NULL,
                            'PENDING',
                            NULL,
                            NULL,
                            NULL,
                            NULL,
                            ?, NULL,
                            0,
                            ?, ?
                        )
                        """,
                detectionUid.toString(),
                facts.tenantId(),
                facts.organizationId(),
                facts.deploymentId(),
                facts.portId(),
                orderId,
                facts.bagId(),
                baselineSnapshot,
                capacity.baselineId(),
                capacity.baselineWeightGrams(),
                facts.deviceConfigVersionId(),
                facts.portConfigSnapshotId(),
                capacity.ruleFingerprint(),
                facts.fullnessMode(),
                facts.configuredFullWeightGrams(),
                facts.fullnessSettleWaitMs(),
                facts.fullnessConfirmationWaitMs(),
                nextSampleAt,
                facts.backendReceivedAt(),
                facts.backendReceivedAt()),
                "insert pending delivery fullness detection");
        Long detectionId = jdbc.queryForObject("""
                        SELECT id
                        FROM rec_fullness_detection
                        WHERE detection_uid = ?
                        """,
                Long.class,
                detectionUid.toString());
        if (detectionId == null) {
            throw new IllegalStateException(
                    "fullness detection id is missing");
        }

        CapacityProjection projection =
                capacityProjection(facts, capacity);
        requireSingle(jdbc.update("""
                        UPDATE rec_port_capacity_state
                        SET latest_stable_total_weight_g = ?,
                            raw_net_weight_g = ?,
                            displayed_fullness_percent = ?,
                            detection_gate = 'PENDING',
                            current_detection_id = ?,
                            current_rule_fingerprint = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND port_id = ?
                          AND detection_gate = 'READY'
                          AND current_detection_id IS NULL
                        """,
                projection.latestTotalWeightGrams(),
                projection.rawNetWeightGrams(),
                projection.displayedFullnessPercent(),
                detectionId,
                capacity.ruleFingerprint(),
                facts.backendReceivedAt(),
                facts.tenantId(),
                facts.organizationId(),
                facts.deploymentId(),
                facts.portId()),
                "open pending delivery fullness gate");
        return new DetectionResult(detectionId, detectionUid);
    }

    private static CapacityProjection capacityProjection(
            DeliveryCompletionPersistenceFacts facts,
            CapacityState capacity) {
        long latest = facts.physicalFact()
                .finalPostCloseMeasurement()
                .reportedWeightGrams();
        if (!"VALID".equals(capacity.baselineState())) {
            return new CapacityProjection(latest, null, null);
        }
        long rawNet = Math.subtractExact(
                latest,
                capacity.baselineWeightGrams());
        BigDecimal displayed = BigDecimal.valueOf(
                        Math.max(0L, rawNet))
                .multiply(BigDecimal.valueOf(100))
                .divide(
                        BigDecimal.valueOf(
                                facts.configuredFullWeightGrams()),
                        2,
                        RoundingMode.HALF_UP);
        return new CapacityProjection(
                latest,
                rawNet,
                displayed);
    }

    private static OrderCalculation calculate(
            DeliveryCompletionPersistenceFacts facts) {
        long before = facts.physicalFact()
                .firstPreOpenMeasurement()
                .reportedWeightGrams();
        long after = facts.physicalFact()
                .finalPostCloseMeasurement()
                .reportedWeightGrams();
        long net = Math.subtractExact(after, before);
        boolean inRange = BigInteger.valueOf(net)
                .abs()
                .compareTo(BigInteger.valueOf(
                        facts.maxReviewAbsWeightGrams())) <= 0;
        if (!inRange) {
            return new OrderCalculation(
                    net,
                    null,
                    null,
                    "INVALID");
        }
        BigDecimal businessWeight = BigDecimal.valueOf(net)
                .divide(
                        BigDecimal.valueOf(1_000),
                        2,
                        RoundingMode.HALF_UP);
        long amount = BigDecimal.valueOf(net)
                .multiply(BigDecimal.valueOf(
                        facts.physicalFact()
                                .unitPriceTenThousandths()))
                .divide(
                        BigDecimal.valueOf(100_000),
                        0,
                        RoundingMode.HALF_UP)
                .longValueExact();
        return new OrderCalculation(
                net,
                businessWeight,
                amount,
                "RELIABLE");
    }

    private static CapacityState capacity(ResultSet rs)
            throws SQLException {
        Long baselineId = nullableLong(
                rs,
                "current_baseline_id");
        Long baselineWeight = nullableLong(
                rs,
                "current_baseline_weight_g");
        Long detectionId = nullableLong(
                rs,
                "current_detection_id");
        return new CapacityState(
                rs.getString("baseline_state"),
                baselineId,
                baselineWeight,
                rs.getString("detection_gate"),
                detectionId,
                rs.getBytes("current_rule_fingerprint"),
                rs.getString("confirmed_fullness_state"));
    }

    private static Long nullableLong(
            ResultSet rs,
            String column) throws SQLException {
        long value = rs.getLong(column);
        return rs.wasNull() ? null : value;
    }

    private static String deliveryOrderNo(UUID sessionUid) {
        return "DO" + sessionUid.toString()
                .replace("-", "");
    }

    private static byte[] nullableDigest(String hex) {
        return hex == null ? null : digest(hex);
    }

    private static byte[] digest(String hex) {
        if (hex == null
                || !hex.matches("[0-9a-f]{64}")) {
            throw new IllegalArgumentException(
                    "SHA-256 must be 64 lowercase hexadecimal characters");
        }
        return HexFormat.of().parseHex(hex);
    }

    private static LocalDateTime utc(Instant instant) {
        return instant == null
                ? null
                : LocalDateTime.ofInstant(
                        instant,
                        ZoneOffset.UTC);
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

    private record DeliveryConfiguration(
            long versionNo,
            byte[] contentSha256,
            long openBalanceFloorCent,
            long maxReviewAbsWeightGrams) {
    }

    private record CapacityState(
            String baselineState,
            Long baselineId,
            Long baselineWeightGrams,
            String detectionGate,
            Long currentDetectionId,
            byte[] ruleFingerprint,
            String confirmedFullnessState) {
    }

    private record OrderCalculation(
            long netWeightGrams,
            BigDecimal businessWeightKg,
            Long amountCent,
            String status) {
    }

    private record CapacityProjection(
            long latestTotalWeightGrams,
            Long rawNetWeightGrams,
            BigDecimal displayedFullnessPercent) {
    }

    private record DetectionResult(
            long id,
            UUID uid) {
    }
}
