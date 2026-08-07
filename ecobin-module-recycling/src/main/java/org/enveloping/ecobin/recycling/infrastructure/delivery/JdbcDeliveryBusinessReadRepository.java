package org.enveloping.ecobin.recycling.infrastructure.delivery;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.OptionalLong;
import java.util.Set;

@Repository
class

JdbcDeliveryBusinessReadRepository
        implements DeliveryBusinessReadRepository {

    static final String FIND_CURRENT_DELIVERY_CONFIGURATION_SQL = """
            SELECT config.open_balance_floor_cent
            FROM rec_organization_delivery_config_head head
            JOIN rec_organization_delivery_config config
              ON config.tenant_id = head.tenant_id
             AND config.organization_id = head.organization_id
             AND config.id = head.current_config_id
             AND config.version_no = head.current_version_no
            WHERE head.tenant_id = ?
              AND head.organization_id = ?
            """;

    static final String FIND_CURRENT_BAGS_SQL = """
            SELECT occupancy.port_id
            FROM rec_bag_current_occupancy occupancy
            WHERE occupancy.tenant_id = ?
              AND occupancy.organization_id = ?
              AND occupancy.occupancy_type = 'PORT_BOUND'
              AND occupancy.port_id IN (%s)
            """;

    static final String FIND_CAPACITY_STATES_SQL = """
            SELECT capacity.port_id,
                   capacity.baseline_state,
                   capacity.displayed_fullness_percent,
                   capacity.detection_gate,
                   CASE
                       WHEN capacity.confirmed_fullness_state = 'FULL'
                        AND capacity.current_bag_id = occupancy.bag_id
                       THEN 'FULL'
                       ELSE 'NOT_FULL'
                   END AS confirmed_fullness_state
            FROM rec_port_capacity_state capacity
            LEFT JOIN rec_bag_current_occupancy occupancy
              ON occupancy.tenant_id = capacity.tenant_id
             AND occupancy.organization_id = capacity.organization_id
             AND occupancy.port_id = capacity.port_id
             AND occupancy.occupancy_type = 'PORT_BOUND'
            WHERE capacity.tenant_id = ?
              AND capacity.organization_id = ?
              AND capacity.asset_id = ?
              AND capacity.port_id IN (%s)
            """;

    static final String FIND_ACTIVE_BASELINE_REMEASUREMENTS_SQL = """
            SELECT measurement.port_id
            FROM rec_port_baseline_measurement measurement
            WHERE measurement.tenant_id = ?
              AND measurement.organization_id = ?
              AND measurement.asset_id = ?
              AND measurement.status = 'PENDING'
              AND measurement.port_id IN (%s)
            """;

    static final String FIND_ACTIVE_CLEAN_OPERATIONS_SQL = """
            SELECT clean.port_id
            FROM rec_clean_operation clean
            WHERE clean.tenant_id = ?
              AND clean.organization_id = ?
              AND clean.asset_id = ?
              AND clean.status IN (
                  'PREPARED',
                  'EDGE_SAVED',
                  'IN_PROGRESS',
                  'RECOVERY_REQUIRED'
              )
              AND clean.port_id IN (%s)
            """;

    static final String FIND_CLEAN_RESTART_INTERLOCKS_SQL = """
            SELECT interlock.port_id
            FROM rec_port_clean_restart_interlock interlock
            WHERE interlock.tenant_id = ?
              AND interlock.organization_id = ?
              AND interlock.asset_id = ?
              AND interlock.port_id IN (%s)
            """;

    static final String FIND_DELIVERY_ORDER_NO_SQL = """
            SELECT delivery_order_no
            FROM rec_delivery_order
            WHERE tenant_id = ?
              AND organization_id = ?
              AND delivery_session_id = ?
            """;

    private final JdbcTemplate jdbc;

    JdbcDeliveryBusinessReadRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public OptionsRows findCurrentOptions(
            long tenantId,
            long organizationId,
            long assetId,
            List<Long> portIds) {
        List<Long> requestedPortIds = requirePortIds(portIds);
        OptionalLong openBalanceFloorCent =
                findOpenBalanceFloorCent(tenantId, organizationId);

        Set<Long> currentBags = new LinkedHashSet<>(jdbc.query(
                withPortPlaceholders(
                        FIND_CURRENT_BAGS_SQL,
                        requestedPortIds.size()),
                (rs, ignored) -> rs.getLong("port_id"),
                scopedPortArguments(
                        tenantId,
                        organizationId,
                        null,
                        requestedPortIds)));

        Map<Long, CapacityRow> capacityByPort = new LinkedHashMap<>();
        jdbc.query(
                withPortPlaceholders(
                        FIND_CAPACITY_STATES_SQL,
                        requestedPortIds.size()),
                rs -> {
                    while (rs.next()) {
                        long portId = rs.getLong("port_id");
                        CapacityRow previous = capacityByPort.put(
                                portId,
                                new CapacityRow(
                                        rs.getString("baseline_state"),
                                        rs.getBigDecimal(
                                                "displayed_fullness_percent"),
                                        rs.getString("detection_gate"),
                                        rs.getString(
                                                "confirmed_fullness_state")));
                        if (previous != null) {
                            throw new IllegalStateException(
                                    "duplicate recycling capacity state");
                        }
                    }
                    return null;
                },
                scopedPortArguments(
                        tenantId,
                        organizationId,
                        assetId,
                        requestedPortIds));

        Set<Long> activeBaselineRemeasurements =
                queryPortIds(
                        FIND_ACTIVE_BASELINE_REMEASUREMENTS_SQL,
                        tenantId,
                        organizationId,
                        assetId,
                        requestedPortIds);
        Set<Long> activeCleanOperations =
                queryPortIds(
                        FIND_ACTIVE_CLEAN_OPERATIONS_SQL,
                        tenantId,
                        organizationId,
                        assetId,
                        requestedPortIds);
        Set<Long> cleanRestartInterlocks =
                queryPortIds(
                        FIND_CLEAN_RESTART_INTERLOCKS_SQL,
                        tenantId,
                        organizationId,
                        assetId,
                        requestedPortIds);

        return new OptionsRows(
                openBalanceFloorCent,
                Set.copyOf(currentBags),
                Map.copyOf(capacityByPort),
                activeBaselineRemeasurements,
                activeCleanOperations,
                cleanRestartInterlocks);
    }

    @Override
    public Optional<String> findDeliveryOrderNo(
            long tenantId,
            long organizationId,
            long deliverySessionId) {
        return jdbc.query(
                        FIND_DELIVERY_ORDER_NO_SQL,
                        (rs, ignored) ->
                                rs.getString("delivery_order_no"),
                        tenantId,
                        organizationId,
                        deliverySessionId)
                .stream()
                .findFirst();
    }

    private OptionalLong findOpenBalanceFloorCent(
            long tenantId,
            long organizationId) {
        return jdbc.query(
                        FIND_CURRENT_DELIVERY_CONFIGURATION_SQL,
                        (rs, ignored) ->
                                rs.getLong("open_balance_floor_cent"),
                        tenantId,
                        organizationId)
                .stream()
                .mapToLong(Long::longValue)
                .findFirst();
    }

    private Set<Long> queryPortIds(
            String sql,
            long tenantId,
            long organizationId,
            long assetId,
            List<Long> portIds) {
        return Set.copyOf(jdbc.query(
                withPortPlaceholders(sql, portIds.size()),
                (rs, ignored) -> rs.getLong("port_id"),
                scopedPortArguments(
                        tenantId,
                        organizationId,
                        assetId,
                        portIds)));
    }

    static String withPortPlaceholders(
            String sql,
            int portCount) {
        if (portCount < 1) {
            throw new IllegalArgumentException(
                    "portCount must be positive");
        }
        return sql.formatted(
                String.join(", ", java.util.Collections.nCopies(
                        portCount,
                        "?")));
    }

    private static Object[] scopedPortArguments(
            long tenantId,
            long organizationId,
            Long assetId,
            List<Long> portIds) {
        List<Object> arguments = new ArrayList<>(
                portIds.size() + (assetId == null ? 2 : 3));
        arguments.add(tenantId);
        arguments.add(organizationId);
        if (assetId != null) {
            arguments.add(assetId);
        }
        arguments.addAll(portIds);
        return arguments.toArray();
    }

    private static List<Long> requirePortIds(List<Long> portIds) {
        List<Long> copy = List.copyOf(portIds);
        if (copy.isEmpty()) {
            throw new IllegalArgumentException(
                    "portIds must not be empty");
        }
        Set<Long> unique = new LinkedHashSet<>();
        for (Long portId : copy) {
            if (portId == null || portId <= 0) {
                throw new IllegalArgumentException(
                        "portIds must contain positive values");
            }
            if (!unique.add(portId)) {
                throw new IllegalArgumentException(
                        "portIds must not contain duplicates");
            }
        }
        return copy;
    }
}
