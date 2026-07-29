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
            SELECT physical_result.deployment_id,
                   physical_result.port_id,
                   physical_result.delivery_session_id,
                   physical_result.id AS physical_result_id,
                   edge_event.event_uid,
                   delivery_session.session_uid,
                   deployment.public_code AS deployment_code,
                   port.port_no
            FROM dev_physical_result physical_result
            JOIN dev_edge_event edge_event
              ON edge_event.tenant_id = physical_result.tenant_id
             AND edge_event.organization_id =
                 physical_result.organization_id
             AND edge_event.deployment_id =
                 physical_result.deployment_id
             AND edge_event.id = physical_result.edge_event_id
             AND edge_event.event_type =
                 physical_result.edge_event_type
            JOIN dev_delivery_session delivery_session
              ON delivery_session.tenant_id =
                 physical_result.tenant_id
             AND delivery_session.organization_id =
                 physical_result.organization_id
             AND delivery_session.deployment_id =
                 physical_result.deployment_id
             AND delivery_session.port_id = physical_result.port_id
             AND delivery_session.id =
                 physical_result.delivery_session_id
            JOIN dev_device_deployment deployment
              ON deployment.tenant_id = physical_result.tenant_id
             AND deployment.organization_id =
                 physical_result.organization_id
             AND deployment.id = physical_result.deployment_id
            JOIN dev_port port
              ON port.tenant_id = physical_result.tenant_id
             AND port.organization_id =
                 physical_result.organization_id
             AND port.deployment_id =
                 physical_result.deployment_id
             AND port.id = physical_result.port_id
            WHERE physical_result.tenant_id = ?
              AND physical_result.organization_id = ?
              AND physical_result.result_type = 'DELIVERY'
              AND physical_result.edge_event_type = 'DELIVERY_COMPLETE'
              AND (
                  physical_result.deployment_id,
                  physical_result.port_id,
                  physical_result.delivery_session_id,
                  physical_result.id
              ) IN (
            """;

    static final String RESOLVE_DEPLOYMENT_FILTER_SQL = """
            SELECT deployment.id AS deployment_id,
                   port.id AS port_id
            FROM dev_device_deployment deployment
            LEFT JOIN dev_port port
              ON port.tenant_id = deployment.tenant_id
             AND port.organization_id = deployment.organization_id
             AND port.deployment_id = deployment.id
             AND port.port_no = ?
            WHERE deployment.tenant_id = ?
              AND deployment.organization_id = ?
              AND deployment.public_code = ?
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
            parameters.add(fact.deploymentKey());
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
    public Optional<DeploymentFilterKeyRow> resolveDeploymentFilter(
            long tenantId,
            long organizationId,
            String deploymentCode,
            Integer portNo) {
        return jdbc.query(
                RESOLVE_DEPLOYMENT_FILTER_SQL,
                (rs, ignored) -> new DeploymentFilterKeyRow(
                        rs.getLong("deployment_id"),
                        rs.getObject("port_id", Long.class)),
                portNo,
                tenantId,
                organizationId,
                deploymentCode).stream().findFirst();
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
                rs.getLong("deployment_id"),
                rs.getLong("port_id"),
                rs.getLong("delivery_session_id"),
                rs.getLong("physical_result_id"),
                UUID.fromString(rs.getString("event_uid")),
                UUID.fromString(rs.getString("session_uid")),
                rs.getString("deployment_code"),
                rs.getInt("port_no"));
    }
}
