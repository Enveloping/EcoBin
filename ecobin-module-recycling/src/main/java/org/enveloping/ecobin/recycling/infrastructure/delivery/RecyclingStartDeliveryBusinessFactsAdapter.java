package org.enveloping.ecobin.recycling.infrastructure.delivery;

import org.enveloping.ecobin.device.api.port.StartDeliveryBusinessFactsPort;
import org.enveloping.ecobin.device.api.query.StartDeliveryBusinessFactsQuery;
import org.enveloping.ecobin.device.api.result.LockedStartDeliveryBusinessFacts;
import org.enveloping.ecobin.device.api.value.DeliveryRuleSnapshot;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.recycling.application.portgeneration.CurrentPortGenerationPolicy;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

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

    static final String LOCK_CURRENT_CAPACITY_SQL = """
            SELECT current_bag_id,
                   baseline_state,
                   current_baseline_id,
                   current_baseline_weight_g,
                   confirmed_fullness_state
            FROM rec_port_capacity_state
            WHERE tenant_id = ?
              AND organization_id = ?
              AND asset_id = ?
              AND port_id = ?
            FOR UPDATE
            """;

    static final String LOAD_CURRENT_BASELINE_SQL = """
            SELECT id,
                   bag_id,
                   baseline_weight_g
            FROM rec_port_weight_baseline
            WHERE tenant_id = ?
              AND organization_id = ?
              AND port_id = ?
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
                (tenantId, organizationId, assetId, portId) ->
                        lockWithinScope(
                                query,
                                tenantId,
                                organizationId,
                                assetId,
                                portId));
    }

    private LockedStartDeliveryBusinessFacts lockWithinScope(
            StartDeliveryBusinessFactsQuery query,
            long tenantId,
            long organizationId,
            long assetId,
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
        rejectCleanRestartInterlock(
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
        verifyCurrentPortGeneration(
                query.fullnessMode(),
                tenantId,
                organizationId,
                assetId,
                portId,
                bag.id());

        return new LockedStartDeliveryBusinessFacts(
                bag.bagUid(),
                bag.bagCode(),
                TransactionBoundDeliverySessionBusinessFactsRef.issue(
                        tenantId,
                        organizationId,
                        configuration.id(),
                        bag.id()));
    }

    private void verifyCurrentPortGeneration(
            String fullnessMode,
            long tenantId,
            long organizationId,
            long assetId,
            long portId,
            long occupiedBagId) {
        List<CapacityAdmissionRow> rows = jdbc.query(
                LOCK_CURRENT_CAPACITY_SQL,
                (rs, ignored) -> new CapacityAdmissionRow(
                        nullableLong(rs.getLong("current_bag_id"),
                                rs.wasNull()),
                        rs.getString("baseline_state"),
                        nullableLong(
                                rs.getLong("current_baseline_id"),
                                rs.wasNull()),
                        nullableLong(
                                rs.getLong("current_baseline_weight_g"),
                                rs.wasNull()),
                        rs.getString("confirmed_fullness_state")),
                tenantId,
                organizationId,
                assetId,
                portId);
        if (rows.size() > 1) {
            throw new IllegalStateException(
                    "duplicate recycling capacity state");
        }
        CapacityAdmissionRow capacity = rows.isEmpty()
                ? null
                : rows.getFirst();
        if (capacity != null
                && isCurrentBagFull(occupiedBagId, capacity)) {
            throw conflict(
                    "DEVICE.PORT_FULL",
                    "当前投口已满，请等待清运后再投递");
        }
        if (!CurrentPortGenerationPolicy
                .requiresWeightBaseline(fullnessMode)) {
            return;
        }
        BaselineRow baseline = loadCurrentBaseline(
                tenantId,
                organizationId,
                portId,
                capacity);
        if (capacity == null
                || !CurrentPortGenerationPolicy
                .hasValidCurrentWeightBaseline(
                        occupiedBagId,
                        capacity.currentBagId(),
                        capacity.baselineState(),
                        capacity.currentBaselineId(),
                        capacity.currentBaselineWeightGrams(),
                        baseline == null ? null : baseline.id(),
                        baseline == null ? null : baseline.bagId(),
                        baseline == null
                                ? null
                                : baseline.weightGrams())) {
            throw conflict(
                    "DEVICE.WEIGHT_BASELINE_MISSING",
                    "当前袋缺少有效重量基准，请先完成清运或空袋重测");
        }
    }

    private BaselineRow loadCurrentBaseline(
            long tenantId,
            long organizationId,
            long portId,
            CapacityAdmissionRow capacity) {
        if (capacity == null
                || capacity.currentBaselineId() == null) {
            return null;
        }
        List<BaselineRow> rows = jdbc.query(
                LOAD_CURRENT_BASELINE_SQL,
                (rs, ignored) -> new BaselineRow(
                        rs.getLong("id"),
                        rs.getLong("bag_id"),
                        rs.getLong("baseline_weight_g")),
                tenantId,
                organizationId,
                portId,
                capacity.currentBaselineId());
        if (rows.size() > 1) {
            throw new IllegalStateException(
                    "duplicate recycling weight baseline");
        }
        return rows.isEmpty() ? null : rows.getFirst();
    }

    static boolean isCurrentBagFull(
            long occupiedBagId,
            CapacityAdmissionRow capacity) {
        return CurrentPortGenerationPolicy.isCurrentBagFull(
                occupiedBagId,
                capacity.currentBagId(),
                capacity.confirmedFullnessState());
    }

    private static Long nullableLong(
            long value,
            boolean wasNull) {
        return wasNull ? null : value;
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

    private void rejectCleanRestartInterlock(
            long tenantId,
            long organizationId,
            long portId) {
        List<Long> rows = jdbc.query("""
                        SELECT source_clean_operation_id
                        FROM rec_port_clean_restart_interlock
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND port_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong(
                        "source_clean_operation_id"),
                tenantId,
                organizationId,
                portId);
        if (!rows.isEmpty()) {
            throw conflict(
                    "CLEAN.RESTARTED_CLEAN_REQUIRED",
                    "上一次清运被设备重启中断，必须先重新完成一次完整清运");
        }
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

    record CapacityAdmissionRow(
            Long currentBagId,
            String baselineState,
            Long currentBaselineId,
            Long currentBaselineWeightGrams,
            String confirmedFullnessState) {
    }

    private record BaselineRow(
            long id,
            long bagId,
            long weightGrams) {
    }

}
