package org.enveloping.ecobin.recycling.infrastructure.delivery;

import org.enveloping.ecobin.device.api.port.StartDeliveryBusinessFactsPort;
import org.enveloping.ecobin.device.api.query.StartDeliveryBusinessFactsQuery;
import org.enveloping.ecobin.device.api.result.LockedStartDeliveryBusinessFacts;
import org.enveloping.ecobin.device.api.value.DeliveryRuleSnapshot;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.HexFormat;
import java.util.List;
import java.util.UUID;

/**
 * Locks the bag/capacity facts only after device has locked the selected port.
 */
@Component
public class RecyclingStartDeliveryBusinessFactsAdapter
        implements StartDeliveryBusinessFactsPort {

    static final String LOCK_CURRENT_BAG_OCCUPANCY_SQL = """
            SELECT bag_id
            FROM rec_bag_current_occupancy
            WHERE tenant_id = ?
              AND organization_id = ?
              AND port_id = ?
              AND occupancy_type = 'PORT_BOUND'
            FOR UPDATE
            """;

    static final String LOAD_BAG_SQL = """
            SELECT id, bag_uid, bag_code
            FROM rec_bag
            WHERE tenant_id = ?
              AND organization_id = ?
              AND id = ?
            """;

    static final String LOCK_DELIVERY_CONFIGURATION_HEAD_SQL = """
            SELECT current_config_id, current_version_no
            FROM rec_organization_delivery_config_head
            WHERE tenant_id = ?
              AND organization_id = ?
            FOR UPDATE
            """;

    static final String LOAD_DELIVERY_CONFIGURATION_SQL = """
            SELECT id,
                   version_no,
                   LOWER(HEX(content_sha256))
                       AS content_sha256,
                   open_balance_floor_cent,
                   max_review_abs_weight_g
            FROM rec_organization_delivery_config
            WHERE tenant_id = ?
              AND organization_id = ?
              AND id = ?
              AND version_no = ?
            """;

    private final JdbcTemplate jdbc;

    public RecyclingStartDeliveryBusinessFactsAdapter(
            JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public LockedStartDeliveryBusinessFacts lockForStart(
            StartDeliveryBusinessFactsQuery query) {
        return query.devicePort().useOnce(
                (tenantId, organizationId, deploymentId, portId) ->
                        lockWithinScope(
                                query,
                                tenantId,
                                organizationId,
                                deploymentId,
                                portId));
    }

    private LockedStartDeliveryBusinessFacts lockWithinScope(
            StartDeliveryBusinessFactsQuery query,
            long tenantId,
            long organizationId,
            long deploymentId,
            long portId) {
        DeliveryConfiguration configuration =
                lockCurrentConfiguration(
                        tenantId,
                        organizationId);
        verifyRule(
                query.expectedDeliveryRule(),
                configuration);
        rejectActivePortWork(
                tenantId,
                organizationId,
                portId);

        List<Long> occupiedBagIds = jdbc.query(
                LOCK_CURRENT_BAG_OCCUPANCY_SQL,
                (rs, ignored) -> rs.getLong("bag_id"),
                tenantId,
                organizationId,
                portId);
        if (occupiedBagIds.size() != 1) {
            throw conflict(
                    "DEVICE.CURRENT_BAG_MISSING",
                    "当前投口没有可用于投递的在位袋");
        }
        List<BagRow> bags = jdbc.query(
                LOAD_BAG_SQL,
                (rs, ignored) -> new BagRow(
                        rs.getLong("id"),
                        UUID.fromString(rs.getString("bag_uid")),
                        rs.getString("bag_code")),
                tenantId,
                organizationId,
                occupiedBagIds.getFirst());
        if (bags.size() != 1) {
            throw conflict(
                    "DEVICE.CURRENT_BAG_MISSING",
                    "当前投口没有可用于投递的在位袋");
        }
        BagRow bag = bags.getFirst();

        List<CapacityRow> capacities = jdbc.query("""
                        SELECT baseline_state,
                               detection_gate,
                               current_detection_id,
                               current_rule_fingerprint,
                               confirmed_fullness_state,
                               current_bag_id
                        FROM rec_port_capacity_state
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND port_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> capacity(rs),
                tenantId,
                organizationId,
                deploymentId,
                portId);
        if (capacities.size() > 1) {
            throw conflict(
                    "DEVICE.PORT_UNAVAILABLE",
                    "当前投口存在重复的容量状态");
        }
        if (!capacities.isEmpty()) {
            requireCapacityEligible(
                    capacities.getFirst(),
                    bag.id());
        }

        return new LockedStartDeliveryBusinessFacts(
                bag.bagUid(),
                bag.bagCode(),
                TransactionBoundDeliverySessionBusinessFactsRef.issue(
                        tenantId,
                        organizationId,
                        configuration.id(),
                        bag.id()));
    }

    private DeliveryConfiguration lockCurrentConfiguration(
            long tenantId,
            long organizationId) {
        List<DeliveryConfigurationHead> heads = jdbc.query(
                LOCK_DELIVERY_CONFIGURATION_HEAD_SQL,
                (rs, ignored) -> new DeliveryConfigurationHead(
                        rs.getLong("current_config_id"),
                        rs.getLong("current_version_no")),
                tenantId,
                organizationId);
        if (heads.size() != 1) {
            throw conflict(
                    "DELIVERY.CONFIGURATION_UNAVAILABLE",
                    "机构还没有可用于投递的当前规则");
        }
        DeliveryConfigurationHead head = heads.getFirst();
        List<DeliveryConfiguration> rows = jdbc.query(
                LOAD_DELIVERY_CONFIGURATION_SQL,
                (rs, ignored) -> new DeliveryConfiguration(
                        rs.getLong("id"),
                        rs.getLong("version_no"),
                        rs.getString("content_sha256"),
                        rs.getLong("open_balance_floor_cent"),
                        rs.getLong("max_review_abs_weight_g")),
                tenantId,
                organizationId,
                head.configId(),
                head.version());
        if (rows.size() != 1) {
            throw conflict(
                    "DELIVERY.CONFIGURATION_UNAVAILABLE",
                    "机构还没有可用于投递的当前规则");
        }
        return rows.getFirst();
    }

    private void rejectActivePortWork(
            long tenantId,
            long organizationId,
            long portId) {
        rejectRows(
                "rec_clean_operation",
                "CLEAN.PORT_OPERATION_ACTIVE",
                "当前投口正在清运",
                tenantId,
                organizationId,
                portId);
        rejectRows(
                "rec_port_baseline_measurement",
                "DEVICE.BASELINE_REMEASUREMENT_ACTIVE",
                "当前投口正在重测重量基准",
                tenantId,
                organizationId,
                portId);
    }

    private void rejectRows(
            String table,
            String code,
            String detail,
            long tenantId,
            long organizationId,
            long portId) {
        String sql = "SELECT id FROM " + table
                + " WHERE tenant_id = ?"
                + " AND organization_id = ?"
                + " AND active_port_id = ? FOR UPDATE";
        List<Long> ids = jdbc.query(
                sql,
                (rs, ignored) -> rs.getLong("id"),
                tenantId,
                organizationId,
                portId);
        if (!ids.isEmpty()) {
            throw conflict(code, detail);
        }
    }

    private static void requireCapacityEligible(
            CapacityRow capacity,
            long currentBagId) {
        if (isCurrentBagFull(
                capacity.fullnessState(),
                capacity.currentBagId(),
                currentBagId)) {
            throw conflict(
                    "DEVICE.PORT_FULL",
                    "当前投口已经满溢");
        }
    }

    static boolean isCurrentBagFull(
            String fullnessState,
            Long reportedBagId,
            long currentBagId) {
        return "FULL".equals(fullnessState)
                && Long.valueOf(currentBagId).equals(reportedBagId);
    }

    private static void verifyRule(
            DeliveryRuleSnapshot expected,
            DeliveryConfiguration current) {
        if (expected.version() != current.version()
                || !expected.contentSha256().equals(
                        current.contentSha256())
                || expected.openBalanceFloorCent()
                != current.openBalanceFloorCent()
                || expected.maxReviewAbsWeightGrams()
                != current.maxReviewAbsWeightGrams()) {
            throw conflict(
                    "DELIVERY.CONFIGURATION_CHANGED",
                    "投递规则已变化，请重新发起投递");
        }
    }

    private static CapacityRow capacity(ResultSet rs)
            throws SQLException {
        byte[] fingerprint =
                rs.getBytes("current_rule_fingerprint");
        Long currentDetection =
                nullableLong(rs, "current_detection_id");
        return new CapacityRow(
                rs.getString("baseline_state"),
                rs.getString("detection_gate"),
                currentDetection,
                fingerprint == null
                        ? null
                        : HexFormat.of().formatHex(fingerprint),
                rs.getString("confirmed_fullness_state"),
                nullableLong(rs, "current_bag_id"));
    }

    private static Long nullableLong(
            ResultSet rs,
            String column) throws SQLException {
        long value = rs.getLong(column);
        return rs.wasNull() ? null : value;
    }

    private static TargetApiException conflict(
            String code,
            String detail) {
        return new TargetApiException(409, code, detail);
    }

    private record DeliveryConfiguration(
            long id,
            long version,
            String contentSha256,
            long openBalanceFloorCent,
            long maxReviewAbsWeightGrams) {
    }

    private record DeliveryConfigurationHead(
            long configId,
            long version) {
    }

    private record BagRow(
            long id,
            UUID bagUid,
            String bagCode) {
    }

    private record CapacityRow(
            String baselineState,
            String detectionGate,
            Long currentDetectionId,
            String ruleFingerprint,
            String fullnessState,
            Long currentBagId) {
    }
}
