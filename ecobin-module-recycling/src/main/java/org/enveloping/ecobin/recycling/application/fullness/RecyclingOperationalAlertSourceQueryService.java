package org.enveloping.ecobin.recycling.application.fullness;

import org.enveloping.ecobin.recycling.api.port.RecyclingOperationalAlertSourcePort;
import org.enveloping.ecobin.recycling.api.result.PortFullnessAlertFact;
import org.enveloping.ecobin.recycling.api.persistence.RecyclingOwnedPortFullnessAlertScopeRefFactory;
import org.enveloping.ecobin.device.api.port.RecyclingDeviceRelationQueryPort;
import org.enveloping.ecobin.recycling.application.devicefacts.RecyclingDeviceRelationBatch;
import org.enveloping.ecobin.recycling.application.devicefacts.RecyclingDeviceRelationBatch.Entry;
import org.enveloping.ecobin.recycling.application.devicefacts.RecyclingDeviceRelationBatch.Kind;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.UUID;

@Service
public class RecyclingOperationalAlertSourceQueryService
        implements RecyclingOperationalAlertSourcePort {

    static final String LOAD_PORT_FULLNESS_ALERT_FACTS_SQL = """
            SELECT capacity.tenant_id, capacity.organization_id,
                   capacity.port_id,
                   capacity.confirmed_fullness_state,
                   state_change.state_change_uid,
                   COALESCE(capacity.last_fullness_reported_at,
                            capacity.updated_at) AS effective_observed_at
            FROM rec_port_capacity_state capacity
            LEFT JOIN rec_fullness_state_change state_change
              ON state_change.tenant_id = capacity.tenant_id
             AND state_change.organization_id = capacity.organization_id
             AND state_change.id =
                 capacity.current_fullness_state_change_id
            WHERE capacity.confirmed_fullness_state IN ('FULL', 'NOT_FULL')
            ORDER BY capacity.port_id
            """;

    private final JdbcTemplate jdbc;
    private final RecyclingOwnedPortFullnessAlertScopeRefFactory refs;
    private final RecyclingDeviceRelationQueryPort deviceFacts;

    public RecyclingOperationalAlertSourceQueryService(
            JdbcTemplate jdbc,
            RecyclingOwnedPortFullnessAlertScopeRefFactory refs,
            RecyclingDeviceRelationQueryPort deviceFacts) {
        this.jdbc = jdbc;
        this.refs = refs;
        this.deviceFacts = deviceFacts;
    }

    @Override
    @Transactional(readOnly = true)
    public List<PortFullnessAlertFact> loadPortFullnessAlertFacts() {
        List<Row> rows = jdbc.query(
                LOAD_PORT_FULLNESS_ALERT_FACTS_SQL, (rs, ignored) -> new Row(
                UUID.randomUUID(),
                rs.getLong("tenant_id"),
                rs.getLong("organization_id"),
                rs.getLong("port_id"),
                rs.getString("confirmed_fullness_state"),
                uuid(rs.getString("state_change_uid")),
                rs.getObject("effective_observed_at",
                        LocalDateTime.class).toInstant(ZoneOffset.UTC)));
        var facts = deviceFacts.resolve(new RecyclingDeviceRelationBatch(
                rows.stream().map(row -> new Entry(
                        row.portToken(), Kind.PORT, row.tenantId(),
                        row.organizationId(), row.portId())).toList()));
        return rows.stream().map(row -> {
            var port = facts.ports().get(row.portToken());
            if (port == null) {
                throw new IllegalStateException(
                        "fullness alert port facts missing");
            }
            return new PortFullnessAlertFact(
                    refs.issue(row.tenantId(), row.organizationId()),
                    port.deploymentCode(), port.portNo(), row.state(),
                    row.stateChangeUid(), row.reportedAt());
        }).toList();
    }

    private static UUID uuid(String value) {
        return value == null ? null : UUID.fromString(value);
    }

    private record Row(
            UUID portToken,
            long tenantId,
            long organizationId,
            long portId,
            String state,
            UUID stateChangeUid,
            java.time.Instant reportedAt) { }
}
