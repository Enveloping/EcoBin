package org.enveloping.ecobin.recycling.application.deliveryorder;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.PreparedStatementCreator;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.jdbc.support.KeyHolder;
import org.springframework.stereotype.Repository;

import java.math.BigDecimal;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

@Repository
class JdbcDeliveryOrderRepository {

    private final JdbcTemplate jdbc;

    JdbcDeliveryOrderRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    long currentHighWatermark(DeliveryOrderScope scope) {
        List<Long> rows = jdbc.query("""
                        SELECT last_visibility_sequence_no
                        FROM rec_organization_order_counter
                        WHERE tenant_id = ?
                          AND organization_id = ?
                        """,
                (rs, ignored) -> rs.getLong(
                        "last_visibility_sequence_no"),
                scope.tenantId(),
                scope.organizationId());
        if (rows.isEmpty()) {
            return 0L;
        }
        if (rows.size() != 1) {
            throw new IllegalStateException(
                    "organization delivery order counter is unavailable");
        }
        return rows.getFirst();
    }

    List<DeliveryOrderSummaryRow> findPage(
            DeliveryOrderPageQuery query) {
        StringBuilder sql = new StringBuilder("""
                SELECT o.id AS order_id,
                       o.delivery_order_no,
                       o.organization_user_id,
                       o.asset_id,
                       o.port_id,
                       o.delivery_session_id,
                       o.physical_result_id,
                       o.device_occurred_at,
                       o.backend_received_at,
                       COALESCE(
                           o.device_occurred_at,
                           o.backend_received_at
                       ) AS sort_occurred_at,
                       o.raw_business_weight_kg,
                       o.raw_amount_cent,
                       o.raw_calculation_status,
                       o.review_status,
                       o.current_revision_no,
                       o.final_business_weight_kg,
                       o.final_amount_cent,
                       EXISTS (
                           SELECT 1
                           FROM rec_delivery_anomaly mismatch
                           WHERE mismatch.delivery_order_id = o.id
                             AND mismatch.anomaly_code =
                                 'DELIVERY_NET_WEIGHT_MISMATCH'
                       ) AS net_weight_inconsistent,
                       (
                           SELECT GROUP_CONCAT(
                               anomaly.anomaly_code
                               ORDER BY anomaly.anomaly_code
                               SEPARATOR ','
                           )
                           FROM rec_delivery_anomaly anomaly
                           WHERE anomaly.delivery_order_id = o.id
                       ) AS anomaly_codes,
                       (
                           SELECT CASE
                               WHEN COUNT(*) = 4
                                AND SUM(
                                    CASE WHEN photo.status = 'AVAILABLE'
                                         THEN 1 ELSE 0 END
                                ) = 4
                               THEN 'COMPLETE'
                               ELSE 'INCOMPLETE'
                           END
                           FROM rec_delivery_photo photo
                           WHERE photo.delivery_order_id = o.id
                       ) AS photo_completeness
                FROM rec_delivery_order o
                WHERE o.tenant_id = ?
                  AND o.organization_id = ?
                  AND o.visibility_sequence_no <= ?
                """);
        List<Object> parameters = new ArrayList<>();
        parameters.add(query.scope().tenantId());
        parameters.add(query.scope().organizationId());
        parameters.add(query.highWatermark());

        if (query.scope().organizationUserId() != null) {
            sql.append(" AND o.organization_user_id = ?");
            parameters.add(query.scope().organizationUserId());
        }
        if (query.reviewStatus() != null) {
            sql.append(" AND o.review_status = ?");
            parameters.add(query.reviewStatus());
        }
        if (query.occurredFrom() != null) {
            sql.append(" AND o.device_occurred_at >= ?");
            parameters.add(query.occurredFrom());
        }
        if (query.occurredTo() != null) {
            sql.append(" AND o.device_occurred_at < ?");
            parameters.add(query.occurredTo());
        }
        if (query.organizationUserId() != null) {
            sql.append(" AND o.organization_user_id = ?");
            parameters.add(query.organizationUserId());
        }
        if (query.assetId() != null) {
            sql.append(" AND o.asset_id = ?");
            parameters.add(query.assetId());
        }
        if (!query.portIds().isEmpty()) {
            sql.append(" AND o.port_id IN (");
            sql.append(String.join(
                    ", ",
                    java.util.Collections.nCopies(
                            query.portIds().size(),
                            "?")));
            sql.append(")");
            parameters.addAll(query.portIds());
        }
        if (query.anomalyCode() != null) {
            sql.append("""
                     AND EXISTS (
                         SELECT 1
                         FROM rec_delivery_anomaly filter_anomaly
                         WHERE filter_anomaly.delivery_order_id = o.id
                           AND filter_anomaly.anomaly_code = ?
                     )
                    """);
            parameters.add(query.anomalyCode());
        }
        if ("COMPLETE".equals(query.photoCompleteness())) {
            sql.append("""
                     AND (
                         SELECT COUNT(*)
                         FROM rec_delivery_photo filter_photo
                         WHERE filter_photo.delivery_order_id = o.id
                           AND filter_photo.status = 'AVAILABLE'
                     ) = 4
                    """);
        } else if ("INCOMPLETE".equals(query.photoCompleteness())) {
            sql.append("""
                     AND (
                         SELECT COUNT(*)
                         FROM rec_delivery_photo filter_photo
                         WHERE filter_photo.delivery_order_id = o.id
                           AND filter_photo.status = 'AVAILABLE'
                     ) < 4
                    """);
        }
        if (query.afterSortTime() != null) {
            sql.append("""
                     AND (
                         COALESCE(
                             o.device_occurred_at,
                             o.backend_received_at
                         ) < ?
                         OR (
                             COALESCE(
                                 o.device_occurred_at,
                                 o.backend_received_at
                             ) = ?
                             AND o.delivery_order_no < ?
                         )
                     )
                    """);
            parameters.add(query.afterSortTime());
            parameters.add(query.afterSortTime());
            parameters.add(query.afterOrderNo());
        }
        sql.append("""
                 ORDER BY COALESCE(
                              o.device_occurred_at,
                              o.backend_received_at
                          ) DESC,
                          o.delivery_order_no DESC
                 LIMIT ?
                """);
        parameters.add(query.limitPlusOne());
        return jdbc.query(
                sql.toString(),
                (rs, ignored) -> summary(rs),
                parameters.toArray());
    }

    Optional<DeliveryOrderRootRow> findDetail(
            DeliveryOrderScope scope,
            String deliveryOrderNo) {
        String userConstraint = scope.organizationUserId() == null
                ? ""
                : " AND o.organization_user_id = ?";
        List<Object> parameters = new ArrayList<>(List.of(
                scope.tenantId(),
                scope.organizationId(),
                deliveryOrderNo));
        if (scope.organizationUserId() != null) {
            parameters.add(scope.organizationUserId());
        }
        return jdbc.query("""
                        SELECT o.id AS order_id,
                               o.delivery_order_no,
                               o.organization_user_id,
                               o.asset_id,
                               o.port_id,
                               o.delivery_session_id,
                               o.physical_result_id,
                               o.device_occurred_at,
                               o.backend_received_at,
                               o.initial_weight_status,
                               o.initial_weight_g,
                               o.final_weight_status,
                               o.final_weight_g,
                               o.raw_net_weight_g,
                               o.raw_business_weight_kg,
                               o.raw_amount_cent,
                               o.raw_calculation_status,
                               o.unit_price_yuan_per_kg,
                               o.negative_weight_anomaly,
                               o.review_status,
                               o.current_revision_no,
                               o.max_review_abs_weight_g,
                               o.final_business_weight_kg,
                               o.final_amount_cent,
                               o.first_approved_at,
                               EXISTS (
                                   SELECT 1
                                   FROM rec_delivery_anomaly mismatch
                                   WHERE mismatch.delivery_order_id = o.id
                                     AND mismatch.anomaly_code =
                                         'DELIVERY_NET_WEIGHT_MISMATCH'
                               ) AS net_weight_inconsistent
                        FROM rec_delivery_order o
                        WHERE o.tenant_id = ?
                          AND o.organization_id = ?
                          AND o.delivery_order_no = ?
                        """ + userConstraint,
                (rs, ignored) -> detailRoot(rs),
                parameters.toArray()).stream().findFirst();
    }

    List<DeliveryAnomalyRow> findAnomalies(long orderId) {
        return jdbc.query("""
                        SELECT category, anomaly_code,
                               CAST(diagnostic_json AS CHAR)
                                   AS diagnostic_json,
                               detected_at
                        FROM rec_delivery_anomaly
                        WHERE delivery_order_id = ?
                        ORDER BY detected_at, id
                        """,
                (rs, ignored) -> new DeliveryAnomalyRow(
                        rs.getString("category"),
                        rs.getString("anomaly_code"),
                        rs.getString("diagnostic_json"),
                        rs.getObject(
                                "detected_at",
                                LocalDateTime.class)),
                orderId);
    }

    List<DeliveryPhotoRow> findPhotos(long orderId) {
        return jdbc.query("""
                        SELECT position, status, object_url,
                               captured_at, missing_reason
                        FROM rec_delivery_photo
                        WHERE delivery_order_id = ?
                        ORDER BY FIELD(
                            position,
                            'BEFORE_INNER',
                            'BEFORE_OUTER',
                            'AFTER_INNER',
                            'AFTER_OUTER'
                        )
                        """,
                (rs, ignored) -> new DeliveryPhotoRow(
                        rs.getString("position"),
                        rs.getString("status"),
                        rs.getString("object_url"),
                        rs.getObject(
                                "captured_at",
                                LocalDateTime.class),
                        rs.getString("missing_reason")),
                orderId);
    }

    List<DeliveryRevisionRow> findRevisions(long orderId) {
        return jdbc.query("""
                        SELECT revision.revision_uid,
                               revision.revision_no,
                               revision.revision_type,
                               revision.decision_type,
                               revision.before_final_weight_kg,
                               revision.before_final_amount_cent,
                               revision.after_final_weight_kg,
                               revision.after_final_amount_cent,
                               revision.amount_delta_cent,
                               revision.reason,
                               revision.reviewer_kind,
                               revision.platform_admin_id,
                               revision.staff_account_id,
                               revision.reviewed_at
                        FROM rec_delivery_revision revision
                        WHERE revision.delivery_order_id = ?
                        ORDER BY revision.revision_no
                        """,
                (rs, ignored) -> revision(rs),
                orderId);
    }

    long lockCurrentOpenBalanceFloor(
            DeliveryOrderScope scope) {
        List<Long> heads = jdbc.query("""
                        SELECT current_config_id
                        FROM rec_organization_delivery_config_head
                        WHERE tenant_id = ?
                          AND organization_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("current_config_id"),
                scope.tenantId(),
                scope.organizationId());
        if (heads.size() != 1) {
            throw new TargetApiException(
                    422,
                    "DELIVERY.CONFIGURATION_UNAVAILABLE",
                    "机构还没有可用于审核入账的当前投递规则");
        }
        List<Long> configurations = jdbc.query("""
                        SELECT open_balance_floor_cent
                        FROM rec_organization_delivery_config
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND id = ?
                        """,
                (rs, ignored) -> rs.getLong(
                        "open_balance_floor_cent"),
                scope.tenantId(),
                scope.organizationId(),
                heads.getFirst());
        if (configurations.size() != 1) {
            throw new TargetApiException(
                    422,
                    "DELIVERY.CONFIGURATION_UNAVAILABLE",
                    "机构还没有可用于审核入账的当前投递规则");
        }
        return configurations.getFirst();
    }

    Optional<LockedDeliveryOrderRow> lockOrder(
            DeliveryOrderScope scope,
            String deliveryOrderNo) {
        return jdbc.query("""
                        SELECT id, delivery_order_no,
                               organization_user_id,
                               unit_price_yuan_per_kg,
                               max_review_abs_weight_g,
                               raw_business_weight_kg,
                               raw_amount_cent,
                               raw_calculation_status,
                               EXISTS (
                                   SELECT 1
                                   FROM rec_delivery_anomaly mismatch
                                   WHERE mismatch.delivery_order_id =
                                         rec_delivery_order.id
                                     AND mismatch.anomaly_code =
                                         'DELIVERY_NET_WEIGHT_MISMATCH'
                               ) AS net_weight_inconsistent,
                               review_status,
                               current_revision_no,
                               current_revision_id,
                               final_business_weight_kg,
                               final_amount_cent,
                               first_approved_at
                        FROM rec_delivery_order
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND delivery_order_no = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> lockedOrder(rs),
                scope.tenantId(),
                scope.organizationId(),
                deliveryOrderNo).stream().findFirst();
    }

    /**
     * Reads the current review projection without taking an order lock.
     * Review previews deliberately use this eventually-stale snapshot and the
     * committing command always locks and recalculates the order again.
     */
    Optional<LockedDeliveryOrderRow> findOrder(
            DeliveryOrderScope scope,
            String deliveryOrderNo) {
        return jdbc.query("""
                        SELECT id, delivery_order_no,
                               organization_user_id,
                               unit_price_yuan_per_kg,
                               max_review_abs_weight_g,
                               raw_business_weight_kg,
                               raw_amount_cent,
                               raw_calculation_status,
                               EXISTS (
                                   SELECT 1
                                   FROM rec_delivery_anomaly mismatch
                                   WHERE mismatch.delivery_order_id =
                                         rec_delivery_order.id
                                     AND mismatch.anomaly_code =
                                         'DELIVERY_NET_WEIGHT_MISMATCH'
                               ) AS net_weight_inconsistent,
                               review_status,
                               current_revision_no,
                               current_revision_id,
                               final_business_weight_kg,
                               final_amount_cent,
                               first_approved_at
                        FROM rec_delivery_order
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND delivery_order_no = ?
                        """,
                (rs, ignored) -> lockedOrder(rs),
                scope.tenantId(),
                scope.organizationId(),
                deliveryOrderNo).stream().findFirst();
    }

    InsertedDeliveryRevision insertRevision(
            DeliveryOrderScope scope,
            LockedDeliveryOrderRow order,
            DeliveryRevisionInsert insert) {
        KeyHolder keyHolder = new GeneratedKeyHolder();
        PreparedStatementCreator creator = connection -> {
            PreparedStatement statement = connection.prepareStatement("""
                    INSERT INTO rec_delivery_revision (
                        revision_uid,
                        tenant_id, organization_id,
                        delivery_order_id,
                        revision_no,
                        previous_revision_id,
                        previous_revision_no,
                        revision_type,
                        decision_type,
                        before_final_weight_kg,
                        before_final_amount_cent,
                        after_final_weight_kg,
                        after_final_amount_cent,
                        amount_delta_cent,
                        reviewer_kind,
                        platform_admin_id,
                        staff_account_id,
                        reason,
                        request_sha256,
                        reviewed_at,
                        created_at
                    ) VALUES (
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
                    """, Statement.RETURN_GENERATED_KEYS);
            int index = 1;
            statement.setString(
                    index++,
                    insert.revisionUid().toString());
            statement.setLong(index++, scope.tenantId());
            statement.setLong(index++, scope.organizationId());
            statement.setLong(index++, order.id());
            statement.setLong(index++, insert.revisionNo());
            nullableLong(
                    statement,
                    index++,
                    insert.previousRevisionId());
            nullableLong(
                    statement,
                    index++,
                    insert.previousRevisionNo());
            statement.setString(index++, insert.revisionType());
            statement.setString(index++, insert.decision());
            nullableDecimal(
                    statement,
                    index++,
                    insert.beforeWeightKg());
            nullableLong(
                    statement,
                    index++,
                    insert.beforeAmountCent());
            statement.setBigDecimal(
                    index++,
                    insert.afterWeightKg());
            statement.setLong(
                    index++,
                    insert.afterAmountCent());
            statement.setLong(
                    index++,
                    insert.amountDeltaCent());
            statement.setString(index++, insert.reviewerKind());
            nullableLong(
                    statement,
                    index++,
                    insert.platformAdminId());
            nullableLong(
                    statement,
                    index++,
                    insert.staffAccountId());
            statement.setString(index++, insert.reason());
            statement.setBytes(index++, insert.requestSha256());
            statement.setObject(index++, insert.reviewedAt());
            statement.setObject(index, insert.reviewedAt());
            return statement;
        };
        int affected = jdbc.update(creator, keyHolder);
        if (affected != 1 || keyHolder.getKey() == null) {
            throw new IllegalStateException(
                    "insert delivery revision affected "
                            + affected + " rows");
        }
        return new InsertedDeliveryRevision(
                keyHolder.getKey().longValue(),
                insert.revisionUid(),
                insert.revisionNo());
    }

    void updateCurrentRevision(
            DeliveryOrderScope scope,
            LockedDeliveryOrderRow order,
            InsertedDeliveryRevision revision,
            BigDecimal finalWeightKg,
            long finalAmountCent,
            LocalDateTime reviewedAt) {
        int affected = jdbc.update("""
                        UPDATE rec_delivery_order
                        SET review_status = 'APPROVED',
                            current_revision_no = ?,
                            current_revision_id = ?,
                            final_business_weight_kg = ?,
                            final_amount_cent = ?,
                            first_approved_at =
                                COALESCE(first_approved_at, ?),
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND id = ?
                          AND review_status = ?
                          AND current_revision_no = ?
                        """,
                revision.revisionNo(),
                revision.id(),
                finalWeightKg,
                finalAmountCent,
                reviewedAt,
                reviewedAt,
                scope.tenantId(),
                scope.organizationId(),
                order.id(),
                order.reviewStatus(),
                order.currentRevisionNo());
        if (affected != 1) {
            throw new IllegalStateException(
                    "delivery order revision projection changed concurrently");
        }
    }

    private static DeliveryOrderSummaryRow summary(ResultSet rs)
            throws SQLException {
        return new DeliveryOrderSummaryRow(
                rs.getLong("order_id"),
                rs.getString("delivery_order_no"),
                rs.getLong("organization_user_id"),
                rs.getLong("asset_id"),
                rs.getLong("port_id"),
                rs.getLong("delivery_session_id"),
                rs.getLong("physical_result_id"),
                rs.getObject(
                        "device_occurred_at",
                        LocalDateTime.class),
                rs.getObject(
                        "backend_received_at",
                        LocalDateTime.class),
                rs.getObject(
                        "sort_occurred_at",
                        LocalDateTime.class),
                rs.getBigDecimal("raw_business_weight_kg"),
                nullableLong(rs, "raw_amount_cent"),
                rs.getString("raw_calculation_status"),
                rs.getBoolean("net_weight_inconsistent"),
                rs.getString("review_status"),
                rs.getLong("current_revision_no"),
                rs.getBigDecimal("final_business_weight_kg"),
                nullableLong(rs, "final_amount_cent"),
                commaSeparated(rs.getString("anomaly_codes")),
                rs.getString("photo_completeness"));
    }

    private static DeliveryOrderRootRow detailRoot(ResultSet rs)
            throws SQLException {
        return new DeliveryOrderRootRow(
                rs.getLong("order_id"),
                rs.getString("delivery_order_no"),
                rs.getLong("organization_user_id"),
                rs.getLong("asset_id"),
                rs.getLong("port_id"),
                rs.getLong("delivery_session_id"),
                rs.getLong("physical_result_id"),
                rs.getObject(
                        "device_occurred_at",
                        LocalDateTime.class),
                rs.getObject(
                        "backend_received_at",
                        LocalDateTime.class),
                rs.getString("initial_weight_status"),
                nullableLong(rs, "initial_weight_g"),
                rs.getString("final_weight_status"),
                nullableLong(rs, "final_weight_g"),
                nullableLong(rs, "raw_net_weight_g"),
                rs.getBigDecimal("raw_business_weight_kg"),
                nullableLong(rs, "raw_amount_cent"),
                rs.getString("raw_calculation_status"),
                rs.getBoolean("net_weight_inconsistent"),
                rs.getBigDecimal("unit_price_yuan_per_kg"),
                rs.getBoolean("negative_weight_anomaly"),
                rs.getString("review_status"),
                rs.getLong("current_revision_no"),
                rs.getLong("max_review_abs_weight_g"),
                rs.getBigDecimal("final_business_weight_kg"),
                nullableLong(rs, "final_amount_cent"),
                rs.getObject(
                        "first_approved_at",
                        LocalDateTime.class));
    }

    private static DeliveryRevisionRow revision(ResultSet rs)
            throws SQLException {
        return new DeliveryRevisionRow(
                UUID.fromString(rs.getString("revision_uid")),
                rs.getLong("revision_no"),
                rs.getString("revision_type"),
                rs.getString("decision_type"),
                rs.getBigDecimal("before_final_weight_kg"),
                nullableLong(rs, "before_final_amount_cent"),
                rs.getBigDecimal("after_final_weight_kg"),
                rs.getLong("after_final_amount_cent"),
                rs.getLong("amount_delta_cent"),
                rs.getString("reason"),
                rs.getString("reviewer_kind"),
                nullableLong(rs, "platform_admin_id"),
                nullableLong(rs, "staff_account_id"),
                rs.getObject(
                        "reviewed_at",
                        LocalDateTime.class));
    }

    private static LockedDeliveryOrderRow lockedOrder(
            ResultSet rs) throws SQLException {
        return new LockedDeliveryOrderRow(
                rs.getLong("id"),
                rs.getString("delivery_order_no"),
                rs.getLong("organization_user_id"),
                rs.getBigDecimal("unit_price_yuan_per_kg"),
                rs.getLong("max_review_abs_weight_g"),
                rs.getBigDecimal("raw_business_weight_kg"),
                nullableLong(rs, "raw_amount_cent"),
                rs.getString("raw_calculation_status"),
                rs.getBoolean("net_weight_inconsistent"),
                rs.getString("review_status"),
                rs.getLong("current_revision_no"),
                nullableLong(rs, "current_revision_id"),
                rs.getBigDecimal("final_business_weight_kg"),
                nullableLong(rs, "final_amount_cent"),
                rs.getObject(
                        "first_approved_at",
                        LocalDateTime.class));
    }

    private static Long nullableLong(
            ResultSet rs,
            String column) throws SQLException {
        long value = rs.getLong(column);
        return rs.wasNull() ? null : value;
    }

    private static void nullableLong(
            PreparedStatement statement,
            int index,
            Long value) throws SQLException {
        if (value == null) {
            statement.setNull(index, java.sql.Types.BIGINT);
        } else {
            statement.setLong(index, value);
        }
    }

    private static void nullableDecimal(
            PreparedStatement statement,
            int index,
            BigDecimal value) throws SQLException {
        if (value == null) {
            statement.setNull(index, java.sql.Types.DECIMAL);
        } else {
            statement.setBigDecimal(index, value);
        }
    }

    private static List<String> commaSeparated(String value) {
        return value == null || value.isBlank()
                ? List.of()
                : Arrays.asList(value.split(","));
    }
}

record DeliveryOrderScope(
        long tenantId,
        long organizationId,
        Long organizationUserId) {

    DeliveryOrderScope {
        if (tenantId <= 0 || organizationId <= 0) {
            throw new IllegalArgumentException(
                    "delivery order scope keys must be positive");
        }
        if (organizationUserId != null && organizationUserId <= 0) {
            throw new IllegalArgumentException(
                    "organizationUserId must be positive");
        }
    }
}

record DeliveryOrderPageQuery(
        DeliveryOrderScope scope,
        long highWatermark,
        LocalDateTime afterSortTime,
        String afterOrderNo,
        String reviewStatus,
        LocalDateTime occurredFrom,
        LocalDateTime occurredTo,
        Long organizationUserId,
        Long assetId,
        List<Long> portIds,
        String anomalyCode,
        String photoCompleteness,
        int limitPlusOne) {

    DeliveryOrderPageQuery {
        portIds = portIds == null
                ? List.of()
                : List.copyOf(portIds);
    }
}

record DeliveryOrderSummaryRow(
        long id,
        String deliveryOrderNo,
        long organizationUserId,
        long assetId,
        long portId,
        long deliverySessionId,
        long physicalResultId,
        LocalDateTime deviceOccurredAt,
        LocalDateTime receivedAt,
        LocalDateTime sortOccurredAt,
        BigDecimal rawWeightKg,
        Long rawAmountCent,
        String rawCalculationStatus,
        boolean netWeightInconsistent,
        String reviewStatus,
        long currentRevisionNo,
        BigDecimal finalWeightKg,
        Long finalAmountCent,
        List<String> anomalyCodes,
        String photoCompleteness) {
}

record DeliveryOrderRootRow(
        long id,
        String deliveryOrderNo,
        long organizationUserId,
        long assetId,
        long portId,
        long deliverySessionId,
        long physicalResultId,
        LocalDateTime deviceOccurredAt,
        LocalDateTime receivedAt,
        String initialWeightStatus,
        Long initialWeightGram,
        String finalWeightStatus,
        Long finalWeightGram,
        Long rawNetWeightGram,
        BigDecimal rawWeightKg,
        Long rawAmountCent,
        String rawCalculationStatus,
        boolean netWeightInconsistent,
        BigDecimal unitPriceYuanPerKg,
        boolean negativeWeightAnomaly,
        String reviewStatus,
        long currentRevisionNo,
        long maxReviewAbsWeightGram,
        BigDecimal finalWeightKg,
        Long finalAmountCent,
        LocalDateTime firstApprovedAt) {
}

record DeliveryAnomalyRow(
        String category,
        String code,
        String diagnosticJson,
        LocalDateTime detectedAt) {
}

record DeliveryPhotoRow(
        String position,
        String status,
        String url,
        LocalDateTime capturedAt,
        String missingReason) {
}

record DeliveryRevisionRow(
        UUID revisionUid,
        long revisionNo,
        String revisionType,
        String decision,
        BigDecimal beforeWeightKg,
        Long beforeAmountCent,
        BigDecimal afterWeightKg,
        long afterAmountCent,
        long amountDeltaCent,
        String reason,
        String reviewerKind,
        Long platformAdminId,
        Long staffAccountId,
        LocalDateTime reviewedAt) {
}

record LockedDeliveryOrderRow(
        long id,
        String deliveryOrderNo,
        long organizationUserId,
        BigDecimal unitPriceYuanPerKg,
        long maxReviewAbsWeightGram,
        BigDecimal rawWeightKg,
        Long rawAmountCent,
        String rawCalculationStatus,
        boolean netWeightInconsistent,
        String reviewStatus,
        long currentRevisionNo,
        Long currentRevisionId,
        BigDecimal finalWeightKg,
        Long finalAmountCent,
        LocalDateTime firstApprovedAt) {
}

record DeliveryRevisionInsert(
        UUID revisionUid,
        long revisionNo,
        Long previousRevisionId,
        Long previousRevisionNo,
        String revisionType,
        String decision,
        BigDecimal beforeWeightKg,
        Long beforeAmountCent,
        BigDecimal afterWeightKg,
        long afterAmountCent,
        long amountDeltaCent,
        String reviewerKind,
        Long platformAdminId,
        Long staffAccountId,
        String reason,
        byte[] requestSha256,
        LocalDateTime reviewedAt) {

    DeliveryRevisionInsert {
        requestSha256 = requestSha256.clone();
    }

    @Override
    public byte[] requestSha256() {
        return requestSha256.clone();
    }
}

record InsertedDeliveryRevision(
        long id,
        UUID revisionUid,
        long revisionNo) {
}
