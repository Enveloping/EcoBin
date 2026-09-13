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
import org.enveloping.ecobin.recycling.api.port.ReliableRecyclingTaskRegistrationPort;
import org.enveloping.ecobin.recycling.api.port.ReliableRecyclingTaskRegistrationPort.Registration;
import org.enveloping.ecobin.recycling.application.photo.RecyclingPhotoStatusService;
import org.enveloping.ecobin.recycling.application.portgeneration.CurrentPortGenerationPolicy;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.math.BigInteger;
import java.math.RoundingMode;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;
import java.util.function.Function;
import java.util.stream.Collectors;

/**
 * 根据可信投递结果创建唯一的待审核回收订单。
 *
 * <p>订单在设备完成上报后创建，不在用户请求开门时预建。满溢由边缘设备判断并作为独立
 * 状态变化事实上报；本用例只投影本次完成重量，不创建或轮询主动满溢采样命令。</p>
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

    static final String LOCK_CAPACITY_SQL = """
            SELECT current_bag_id,
                   baseline_state,
                   current_baseline_id,
                   current_baseline_weight_g,
                   detection_gate,
                   current_detection_id,
                   current_rule_fingerprint,
                   confirmed_fullness_state
            FROM rec_port_capacity_state
            WHERE tenant_id = ?
              AND organization_id = ?
              AND asset_id = ?
              AND port_id = ?
            FOR UPDATE
            """;

    static final String LOAD_CAPACITY_BASELINE_SQL = """
            SELECT id,
                   bag_id,
                   baseline_weight_g
            FROM rec_port_weight_baseline
            WHERE tenant_id = ?
              AND organization_id = ?
              AND port_id = ?
              AND id = ?
            """;

    private final CompleteDeliveryDeviceParticipationPort deviceCompletion;
    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final RecyclingPhotoStatusService photoStatusService;
    private final ReliableRecyclingTaskRegistrationPort tasks;

    public ApplyDeliveryCompleteService(
            CompleteDeliveryDeviceParticipationPort deviceCompletion,
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            RecyclingPhotoStatusService photoStatusService,
            ReliableRecyclingTaskRegistrationPort tasks) {
        this.deviceCompletion = deviceCompletion;
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.photoStatusService = photoStatusService;
        this.tasks = tasks;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public TrustedDeviceEventApplyResult apply(
            TrustedDeviceInboxEvent event) {
        // device 先校验并保存不可变物理事实，再通过一次性 writer 回调本服务建单。
        // 两个模块加入同一个外层事务，任何一边失败都会整体回滚。
        return deviceCompletion.complete(
                event,
                reference -> reference.useOnce(
                        this::persistBusinessFacts));
    }

    private DeliveryCompletionBusinessResult persistBusinessFacts(
            DeliveryCompletionPersistenceFacts facts) {
        // 不能只相信完成载荷中的袋和价格：重新核对开始时冻结的配置、当前袋绑定关系，
        // 并锁住容量投影，防止清运换袋和投递完成并发穿插。
        DeliveryConfiguration configuration =
                requireFrozenDeliveryConfiguration(facts);
        requireCurrentBag(facts);
        CapacityState capacity = lockCapacity(facts);
        String capacityProjectionBlockReason =
                capacityProjectionBlockReason(
                        facts.bagId(),
                        capacity);

        long visibilitySequence = nextVisibilitySequence(facts);
        String orderNo = deliveryOrderNo(
                facts.physicalFact().sessionUid());
        OrderCalculation calculation = calculate(facts);
        // 一次 session 只生成一张 PENDING 订单；设备原始事实保留不变，审核结果后续
        // 通过 revision 追加，钱包此处完全不入账。
        long orderId = insertOrder(
                facts,
                configuration,
                visibilitySequence,
                orderNo,
                calculation);

        // 异常和照片都是订单证据：异常不会静默改重量，照片暂缺也不阻止建单。
        insertAnomalies(
                facts,
                orderId,
                calculation,
                capacity,
                capacityProjectionBlockReason);
        registerAutomaticReviewIfEligible(
                facts,
                orderId,
                orderNo,
                calculation,
                configuration);
        insertPhotos(facts, orderId);
        photoStatusService.mergeStagedDeliveryFacts(
                facts, orderId);
        if (capacityProjectionBlockReason == null
                && facts.physicalFact().finalPostCloseMeasurement().weightValueAvailable()) {
            projectCapacityObservation(facts, capacity);
        }

        return new DeliveryCompletionBusinessResult(
                orderNo,
                List.of(new DeliveryCompletionResultReference(
                        "DELIVERY_ORDER",
                        orderNo)));
    }

    private DeliveryConfiguration requireFrozenDeliveryConfiguration(
            DeliveryCompletionPersistenceFacts facts) {
        List<DeliveryConfiguration> rows = jdbc.query("""
                        SELECT version_no,
                               content_sha256,
                               review_mode,
                               automatic_review_max_amount_cent,
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
                        rs.getString("review_mode"),
                        nullableLong(
                                rs,
                                "automatic_review_max_amount_cent"),
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
        List<CapacityState> rows = jdbc.query(
                LOCK_CAPACITY_SQL,
                (rs, ignored) -> capacity(rs),
                facts.tenantId(),
                facts.organizationId(),
                facts.assetId(),
                facts.portId());
        if (rows.size() > 1) {
            throw new IllegalStateException(
                    "duplicate delivery capacity state");
        }
        if (rows.isEmpty()) {
            return null;
        }
        CapacityState capacity = rows.getFirst();
        if (capacity.baselineId() == null) {
            return capacity;
        }
        List<BaselineIdentity> baselines = jdbc.query(
                LOAD_CAPACITY_BASELINE_SQL,
                (rs, ignored) -> new BaselineIdentity(
                        rs.getLong("id"),
                        rs.getLong("bag_id"),
                        rs.getLong("baseline_weight_g")),
                facts.tenantId(),
                facts.organizationId(),
                facts.portId(),
                capacity.baselineId());
        if (baselines.size() > 1) {
            throw new IllegalStateException(
                    "duplicate delivery capacity baseline");
        }
        BaselineIdentity baseline = baselines.isEmpty()
                ? null
                : baselines.getFirst();
        return new CapacityState(
                capacity.currentBagId(),
                capacity.baselineState(),
                capacity.baselineId(),
                capacity.baselineWeightGrams(),
                baseline == null ? null : baseline.id(),
                baseline == null ? null : baseline.bagId(),
                baseline == null ? null : baseline.weightGrams(),
                capacity.detectionGate(),
                capacity.currentDetectionId(),
                capacity.ruleFingerprint(),
                capacity.confirmedFullnessState());
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
                            asset_id, port_id,
                            device_config_version_id,
                            delivery_config_version_id,
                            delivery_config_version_no,
                            delivery_config_content_sha256,
                            review_mode_snapshot,
                            automatic_review_max_amount_cent_snapshot,
                            automatic_review_due_at,
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
                            ?,
                            ?,
                            ?,
                            ?, ?,
                            ?,
                            ?,
                            ?,
                            'RELIABLE', ?,
                            ?, ?,
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
                facts.assetId(),
                facts.portId(),
                facts.deviceConfigVersionId(),
                facts.deliveryConfigVersionId(),
                configuration.versionNo(),
                facts.deliveryConfigContentSha256(),
                configuration.reviewMode(),
                configuration.automaticReviewMaxAmountCent(),
                automaticReviewDueAt(
                        configuration.reviewMode(),
                        facts.backendReceivedAt()),
                facts.unitPriceYuanPerKg(),
                facts.openBalanceFloorCent(),
                facts.bagId(),
                facts.bagUid().toString(),
                facts.bagCode(),
                facts.negativeWeightThresholdGrams(),
                facts.maxReviewAbsWeightGrams(),
                before.reportedWeightGrams(),
                after.weightValueAvailable() ? "RELIABLE" : "UNAVAILABLE",
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
            OrderCalculation calculation,
            CapacityState capacity,
            String capacityProjectionBlockReason) {
        DeliveryCompletePhysicalFact physical =
                facts.physicalFact();
        if ("UNAVAILABLE".equals(calculation.status())) {
            DeliveryCompleteMeasurement failed = physical.finalPostCloseMeasurement();
            insertAnomaly(facts, orderId, "SYSTEM", "TERMINAL_WEIGHT_FAILURE",
                    Map.of("measurementUid", failed.measurementUid().toString(),
                            "faultCode", failed.faultCode(),
                            "measurementElapsedMs", failed.measurementElapsedMs(),
                            "sampleCount", failed.sampleCount()));
        }
        if (!Objects.equals(calculation.netWeightGrams(),
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
        LocalDateTime occurredAt = utc(physical.deviceOccurredAt());
        if ("SYNCED".equals(physical.clockQuality())
                && occurredAt != null
                && facts.backendReceivedAt().isAfter(
                occurredAt.plusMinutes(15))) {
            insertAnomaly(
                    facts,
                    orderId,
                    "SYSTEM",
                    "DELIVERY_COMPLETION_DELAYED",
                    Map.of(
                            "deviceOccurredAt", occurredAt.toString(),
                            "backendReceivedAt",
                            facts.backendReceivedAt().toString(),
                            "maximumDelayMinutes", 15));
        }
        if (capacityProjectionBlockReason != null) {
            Map<String, Object> diagnostic = new LinkedHashMap<>();
            diagnostic.put(
                    "reason",
                    capacityProjectionBlockReason);
            diagnostic.put(
                    "expectedCurrentBagId",
                    facts.bagId());
            diagnostic.put(
                    "capacityCurrentBagId",
                    capacity == null
                            ? null
                            : capacity.currentBagId());
            diagnostic.put(
                    "capacityBaselineId",
                    capacity == null
                            ? null
                            : capacity.baselineId());
            diagnostic.put(
                    "baselineBagId",
                    capacity == null
                            ? null
                            : capacity.baselineBagId());
            insertAnomaly(
                    facts,
                    orderId,
                    "SYSTEM",
                    "CAPACITY_PROJECTION_SKIPPED",
                    diagnostic);
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

    private void projectCapacityObservation(
            DeliveryCompletionPersistenceFacts facts,
            CapacityState capacity) {
        // 这里只更新重量推导的展示值。能否开始下一次投递只认边缘设备针对当前袋
        // 上报的明确 FULL 状态，不能把百分比、旧袋结果或“未上报”自行解释为 FULL。
        CapacityProjection projection =
                capacityProjection(facts, capacity);
        requireSingle(jdbc.update("""
                        UPDATE rec_port_capacity_state
                        SET latest_stable_total_weight_g = ?,
                            raw_net_weight_g = ?,
                            displayed_fullness_percent = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND port_id = ?
                        """,
                projection.latestTotalWeightGrams(),
                projection.rawNetWeightGrams(),
                projection.displayedFullnessPercent(),
                facts.backendReceivedAt(),
                facts.tenantId(),
                facts.organizationId(),
                facts.assetId(),
                facts.portId()),
                "project post-delivery capacity observation");
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

    private void registerAutomaticReviewIfEligible(
            DeliveryCompletionPersistenceFacts facts,
            long orderId,
            String orderNo,
            OrderCalculation calculation,
            DeliveryConfiguration configuration) {
        if ("ALL_MANUAL".equals(configuration.reviewMode())) {
            return;
        }
        Integer anomalies = jdbc.queryForObject("""
                SELECT COUNT(*)
                FROM rec_delivery_anomaly
                WHERE tenant_id = ?
                  AND organization_id = ?
                  AND delivery_order_id = ?
                  AND category IN ('USER', 'SYSTEM')
                """, Integer.class, facts.tenantId(),
                facts.organizationId(), orderId);
        boolean eligible = "RELIABLE".equals(calculation.status())
                && calculation.netWeightGrams() >= 0
                && automaticReviewAmountWithinLimit(
                        calculation.amountCent(),
                        configuration.automaticReviewMaxAmountCent())
                && !facts.physicalFact().negativeWeightAnomaly()
                && (anomalies == null || anomalies == 0);
        if (!eligible) {
            return;
        }
        LocalDateTime dueAt = automaticReviewDueAt(
                configuration.reviewMode(), facts.backendReceivedAt());
        String snapshot = "{\"deliveryOrderNo\":\""
                + orderNo + "\",\"reviewMode\":\""
                + configuration.reviewMode() + "\"}";
        tasks.register(new Registration(
                facts.tenantId(),
                facts.organizationId(),
                "AUTO_REVIEW_DELIVERY_ORDER",
                "AUTO_REVIEW_DELIVERY:" + orderNo,
                "DELIVERY_ORDER",
                orderNo,
                1,
                snapshot,
                sha256(snapshot),
                20,
                dueAt));
    }

    static boolean automaticReviewAmountWithinLimit(
            Long amountCent,
            Long maximumAmountCent) {
        return amountCent != null
                && amountCent >= 0
                && maximumAmountCent != null
                && maximumAmountCent >= 0
                && amountCent <= maximumAmountCent;
    }

    private static LocalDateTime automaticReviewDueAt(
            String reviewMode,
            LocalDateTime receivedAt) {
        return switch (reviewMode) {
            case "ALL_MANUAL" -> null;
            case "NORMAL_AUTO_IMMEDIATE" -> receivedAt;
            case "NORMAL_AUTO_AFTER_24H" -> receivedAt.plusHours(24);
            case "NORMAL_AUTO_AFTER_48H" -> receivedAt.plusHours(48);
            default -> throw new IllegalStateException(
                    "unsupported delivery review mode " + reviewMode);
        };
    }

    private static byte[] sha256(String value) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(
                    value.getBytes(StandardCharsets.UTF_8));
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException(impossible);
        }
    }

    static String capacityProjectionBlockReason(
            long currentBagId,
            CapacityState capacity) {
        if (capacity == null) {
            return "CAPACITY_STATE_MISSING";
        }
        if (!Objects.equals(
                capacity.currentBagId(),
                currentBagId)) {
            return "CAPACITY_CURRENT_BAG_MISMATCH";
        }
        if ("VALID".equals(capacity.baselineState())
                && !CurrentPortGenerationPolicy
                .hasValidCurrentWeightBaseline(
                        currentBagId,
                        capacity.currentBagId(),
                        capacity.baselineState(),
                        capacity.baselineId(),
                        capacity.baselineWeightGrams(),
                        capacity.baselineRecordId(),
                        capacity.baselineBagId(),
                        capacity.baselineRecordWeightGrams())) {
            return "WEIGHT_BASELINE_GENERATION_MISMATCH";
        }
        return null;
    }

    private static OrderCalculation calculate(
            DeliveryCompletionPersistenceFacts facts) {
        // Device participation already verified the exact timeout shape and frozen identity.
        // Keep the failed physical result immutable; only a later human revision may value it.
        if ("TERMINAL_WEIGHT_FAILURE".equals(facts.physicalFact().completionReason())) {
            return new OrderCalculation(null, null, null, "UNAVAILABLE");
        }
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
                nullableLong(rs, "current_bag_id"),
                rs.getString("baseline_state"),
                baselineId,
                baselineWeight,
                null,
                null,
                null,
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
            String reviewMode,
            Long automaticReviewMaxAmountCent,
            long openBalanceFloorCent,
            long maxReviewAbsWeightGrams) {
    }

    record CapacityState(
            Long currentBagId,
            String baselineState,
            Long baselineId,
            Long baselineWeightGrams,
            Long baselineRecordId,
            Long baselineBagId,
            Long baselineRecordWeightGrams,
            String detectionGate,
            Long currentDetectionId,
            byte[] ruleFingerprint,
            String confirmedFullnessState) {
    }

    private record BaselineIdentity(
            long id,
            long bagId,
            long weightGrams) {
    }

    private record OrderCalculation(
            Long netWeightGrams,
            BigDecimal businessWeightKg,
            Long amountCent,
            String status) {
    }

    private record CapacityProjection(
            long latestTotalWeightGrams,
            Long rawNetWeightGrams,
            BigDecimal displayedFullnessPercent) {
    }

}
