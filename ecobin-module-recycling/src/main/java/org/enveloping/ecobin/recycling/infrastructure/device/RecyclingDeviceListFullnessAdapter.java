package org.enveloping.ecobin.recycling.infrastructure.device;

import org.enveloping.ecobin.device.api.persistence.RecyclingDevicePortRef;
import org.enveloping.ecobin.device.api.port.DeviceListFullnessQueryPort;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

@Component
public class RecyclingDeviceListFullnessAdapter implements DeviceListFullnessQueryPort {
    private final JdbcTemplate jdbc;

    public RecyclingDeviceListFullnessAdapter(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY, readOnly = true)
    public Map<UUID, UUID> currentReports(Map<UUID, RecyclingDevicePortRef> ports) {
        if (ports.isEmpty()) return Map.of();
        List<Object> args = new ArrayList<>();
        List<String> predicates = new ArrayList<>();
        Map<Long, UUID> tokens = new LinkedHashMap<>();
        ports.forEach((token, ref) -> ref.consumeOnce((tenant, organization, port) -> {
            predicates.add("(capacity.tenant_id = ? AND capacity.organization_id = ? AND capacity.port_id = ?)");
            args.add(tenant);
            args.add(organization);
            args.add(port);
            tokens.put(port, token);
            return null;
        }));
        Map<UUID, UUID> result = new LinkedHashMap<>();
        jdbc.query("""
                SELECT capacity.port_id, report.state_change_uid
                FROM rec_port_capacity_state capacity
                JOIN rec_bag_current_occupancy bag
                  ON bag.tenant_id = capacity.tenant_id
                 AND bag.organization_id = capacity.organization_id
                 AND bag.port_id = capacity.port_id
                 AND bag.bag_id = capacity.current_bag_id
                 AND bag.occupancy_type = 'PORT_BOUND'
                JOIN rec_fullness_state_change report
                  ON report.id = capacity.current_fullness_state_change_id
                 AND report.tenant_id = capacity.tenant_id
                 AND report.organization_id = capacity.organization_id
                 AND report.port_id = capacity.port_id
                 AND report.bag_id = bag.bag_id
                 AND report.disposition IN ('APPLIED', 'NO_STATE_CHANGE')
                WHERE
                """ + String.join(" OR ", predicates), rs -> {
            result.put(tokens.get(rs.getLong("port_id")),
                    UUID.fromString(rs.getString("state_change_uid")));
        }, args.toArray());
        return Map.copyOf(result);
    }
}
