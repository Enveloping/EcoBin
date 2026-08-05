package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.port.DeviceOperationalAlertSourcePort;
import org.enveloping.ecobin.device.api.result.DeviceFaultAlertFact;
import org.enveloping.ecobin.device.api.persistence.DeviceOwnedFaultAlertScopeRefFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.UUID;

@Service
public class DeviceOperationalAlertSourceQueryService
        implements DeviceOperationalAlertSourcePort {

    private final JdbcTemplate jdbc;
    private final DeviceOwnedFaultAlertScopeRefFactory refs;

    public DeviceOperationalAlertSourceQueryService(
            JdbcTemplate jdbc,
            DeviceOwnedFaultAlertScopeRefFactory refs) {
        this.jdbc = jdbc;
        this.refs = refs;
    }

    @Override
    @Transactional(readOnly = true)
    public List<DeviceFaultAlertFact> loadDeviceFaultAlertFacts(
            List<UUID> currentlyOpenAlertFaultUids) {
        List<UUID> referenced = List.copyOf(currentlyOpenAlertFaultUids);
        String referencedSql = referenced.isEmpty()
                ? "" : " OR fault.fault_uid IN ("
                + "?,".repeat(referenced.size() - 1) + "?)";
        return jdbc.query("""
                SELECT fault.tenant_id, fault.organization_id,
                       fault.fault_uid, deployment.public_code,
                       port.port_no, fault.component_type,
                       fault.fault_code, fault.impact_level, fault.status,
                       fault.first_detected_at, fault.last_detected_at,
                       fault.recovered_at
                FROM dev_device_fault_event fault
                JOIN dev_device_deployment deployment
                  ON deployment.tenant_id = fault.tenant_id
                 AND deployment.organization_id = fault.organization_id
                 AND deployment.id = fault.deployment_id
                LEFT JOIN dev_port port
                  ON port.tenant_id = fault.tenant_id
                 AND port.organization_id = fault.organization_id
                 AND port.id = fault.port_id
                WHERE fault.fault_code <> 'INITIAL_COMMISSIONING'
                  AND (fault.status = 'OPEN'
                """ + referencedSql + ")" + """
                ORDER BY fault.id
                """, (rs, ignored) -> new DeviceFaultAlertFact(
                refs.issue(rs.getLong("tenant_id"),
                        rs.getLong("organization_id")),
                UUID.fromString(rs.getString("fault_uid")),
                rs.getString("public_code"),
                (Integer) rs.getObject("port_no"),
                rs.getString("component_type"),
                rs.getString("fault_code"),
                rs.getString("impact_level"),
                rs.getString("status"),
                instant(rs.getObject("first_detected_at",
                        LocalDateTime.class)),
                instant(rs.getObject("last_detected_at",
                        LocalDateTime.class)),
                instant(rs.getObject("recovered_at",
                        LocalDateTime.class))),
                referenced.stream().map(UUID::toString).toArray());
    }

    private static java.time.Instant instant(LocalDateTime value) {
        return value == null ? null : value.toInstant(ZoneOffset.UTC);
    }
}
