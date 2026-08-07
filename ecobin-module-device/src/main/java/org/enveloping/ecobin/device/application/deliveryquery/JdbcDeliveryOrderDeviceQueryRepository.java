package org.enveloping.ecobin.device.application.deliveryquery;

import org.enveloping.ecobin.device.api.persistence.DeliveryOrderDeviceFactsRef;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

@Repository
class JdbcDeliveryOrderDeviceQueryRepository
        implements DeliveryOrderDeviceQueryRepository {

    static final String FIND_FACTS_SQL_PREFIX = """
            SELECT physical_result.asset_id,
                   physical_result.port_id,
                   physical_result.delivery_session_id,
                   physical_result.id AS physical_result_id,
                   edge_event.event_uid,
                   delivery_session.session_uid,
                   asset.device_public_code,
                   port.port_no
            FROM dev_physical_result physical_result
            JOIN dev_edge_event edge_event
              ON edge_event.tenant_id = physical_result.tenant_id
             AND edge_event.organization_id =
                 physical_result.organization_id
             AND edge_event.asset_id =
                 physical_result.asset_id
             AND edge_event.id = physical_result.edge_event_id
             AND edge_event.event_type =
                 physical_result.edge_event_type
            JOIN dev_delivery_session delivery_session
              ON delivery_session.tenant_id =
                 physical_result.tenant_id
             AND delivery_session.organization_id =
                 physical_result.organization_id
             AND delivery_session.asset_id =
                 physical_result.asset_id
             AND delivery_session.port_id = physical_result.port_id
             AND delivery_session.id =
                 physical_result.delivery_session_id
            JOIN dev_device_asset asset
              ON asset.tenant_id = physical_result.tenant_id
             AND asset.organization_id =
                 physical_result.organization_id
             AND asset.id = physical_result.asset_id
            JOIN dev_port port
              ON port.tenant_id = physical_result.tenant_id
             AND port.organization_id =
                 physical_result.organization_id
             AND port.asset_id =
                 physical_result.asset_id
             AND port.id = physical_result.port_id
            WHERE physical_result.tenant_id = ?
              AND physical_result.organization_id = ?
              AND physical_result.result_type = 'DELIVERY'
              AND physical_result.edge_event_type = 'DELIVERY_COMPLETE'
              AND (
                  physical_result.asset_id,
                  physical_result.port_id,
                  physical_result.delivery_session_id,
                  physical_result.id
              ) IN (
            """;

    static final String RESOLVE_ASSET_FILTER_SQL = """
            SELECT asset.id AS asset_id,
                   port.id AS port_id
            FROM dev_device_asset asset
            LEFT JOIN dev_port port
              ON port.tenant_id = asset.tenant_id
             AND port.organization_id = asset.organization_id
             AND port.asset_id = asset.id
             AND port.port_no = ?
            WHERE asset.tenant_id = ?
              AND asset.organization_id = ?
              AND asset.device_public_code = ?
            """;

    static final String FIND_PORT_FILTER_KEYS_SQL = """
            SELECT port.id AS port_id
            FROM dev_port port
            WHERE port.tenant_id = ?
              AND port.organization_id = ?
              AND port.port_no = ?
            ORDER BY port.id
            """;

    private final JdbcTemplate jdbc;

    JdbcDeliveryOrderDeviceQueryRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public List<ResolvedFactRow> findFacts(
            long tenantId,
            long organizationId,
            List<DeliveryOrderDeviceFactsRef.FactKey> facts) {
        if (facts.isEmpty()) {
            return List.of();
        }
        var parameters = new ArrayList<Object>(
                2 + facts.size() * 4);
        parameters.add(tenantId);
        parameters.add(organizationId);
        for (DeliveryOrderDeviceFactsRef.FactKey fact : facts) {
            parameters.add(fact.assetKey());
            parameters.add(fact.portKey());
            parameters.add(fact.deliverySessionKey());
            parameters.add(fact.physicalResultKey());
        }
        return jdbc.query(
                factsSql(facts.size()),
                JdbcDeliveryOrderDeviceQueryRepository::fact,
                parameters.toArray());
    }

    @Override
    public Optional<AssetFilterKeyRow> resolveAssetFilter(
            long tenantId,
            long organizationId,
            String deviceCode,
            Integer portNo) {
        return jdbc.query(
                RESOLVE_ASSET_FILTER_SQL,
                (rs, ignored) -> new AssetFilterKeyRow(
                        rs.getLong("asset_id"),
                        rs.getObject("port_id", Long.class)),
                portNo,
                tenantId,
                organizationId,
                deviceCode).stream().findFirst();
    }

    @Override
    public List<Long> findPortFilterKeys(
            long tenantId,
            long organizationId,
            int portNo) {
        return jdbc.query(
                FIND_PORT_FILTER_KEYS_SQL,
                (rs, ignored) -> rs.getLong("port_id"),
                tenantId,
                organizationId,
                portNo);
    }

    static String factsSql(int factCount) {
        if (factCount <= 0) {
            throw new IllegalArgumentException(
                    "factCount must be positive");
        }
        String tuples = String.join(
                ", ",
                Collections.nCopies(
                        factCount,
                        "(?, ?, ?, ?)"));
        return FIND_FACTS_SQL_PREFIX + tuples + ")";
    }

    private static ResolvedFactRow fact(
            ResultSet rs,
            int rowNumber) throws SQLException {
        return new ResolvedFactRow(
                rs.getLong("asset_id"),
                rs.getLong("port_id"),
                rs.getLong("delivery_session_id"),
                rs.getLong("physical_result_id"),
                UUID.fromString(rs.getString("event_uid")),
                UUID.fromString(rs.getString("session_uid")),
                rs.getString("device_public_code"),
                rs.getInt("port_no"));
    }
}
