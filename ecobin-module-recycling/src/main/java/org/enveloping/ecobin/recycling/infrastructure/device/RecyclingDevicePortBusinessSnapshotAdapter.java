package org.enveloping.ecobin.recycling.infrastructure.device;

import org.enveloping.ecobin.device.api.port.DevicePortBusinessSnapshotPort;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.util.List;

/**
 * Recycling-owned Adapter for the device runtime projection.
 */
@Component
public class RecyclingDevicePortBusinessSnapshotAdapter
        implements DevicePortBusinessSnapshotPort {

    private final JdbcTemplate jdbc;

    public RecyclingDevicePortBusinessSnapshotAdapter(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    @Transactional(
            propagation = Propagation.MANDATORY,
            readOnly = true)
    public DevicePortBusinessSnapshot find(
            String tenantCode,
            String organizationCode,
            String deploymentCode,
            int portNo) {
        List<DevicePortBusinessSnapshot> rows = jdbc.query("""
                        SELECT
                            CASE WHEN b.port_id IS NULL THEN 0 ELSE 1 END
                                AS current_bag_present,
                            COALESCE(c.baseline_state, 'UNINITIALIZED')
                                AS baseline_state,
                            COALESCE(c.detection_gate, 'UNKNOWN')
                                AS detection_gate,
                            COALESCE(c.confirmed_fullness_state, 'UNKNOWN')
                                AS fullness_state,
                            c.displayed_fullness_percent,
                            EXISTS (
                                SELECT 1
                                FROM rec_clean_operation clean
                                WHERE clean.tenant_id = d.tenant_id
                                  AND clean.organization_id =
                                      d.organization_id
                                  AND clean.deployment_id = d.id
                                  AND clean.port_id = p.id
                                  AND clean.status IN (
                                      'PREPARED',
                                      'EDGE_SAVED',
                                      'IN_PROGRESS',
                                      'RECOVERY_REQUIRED'
                                  )
                            ) AS clean_operation_active
                        FROM iam_tenant t
                        JOIN iam_organization o
                          ON o.tenant_id = t.id
                        JOIN dev_device_deployment d
                          ON d.tenant_id = t.id
                         AND d.organization_id = o.id
                        JOIN dev_port p
                          ON p.tenant_id = d.tenant_id
                         AND p.organization_id = d.organization_id
                         AND p.deployment_id = d.id
                        LEFT JOIN rec_bag_current_occupancy b
                          ON b.tenant_id = p.tenant_id
                         AND b.organization_id = p.organization_id
                         AND b.port_id = p.id
                         AND b.occupancy_type = 'PORT_BOUND'
                        LEFT JOIN rec_port_capacity_state c
                          ON c.tenant_id = p.tenant_id
                         AND c.organization_id = p.organization_id
                         AND c.deployment_id = p.deployment_id
                         AND c.port_id = p.id
                        WHERE t.tenant_code = ?
                          AND o.organization_code = ?
                          AND d.public_code = ?
                          AND p.port_no = ?
                        """,
                (rs, ignored) -> {
                    BigDecimal fullness =
                            rs.getBigDecimal("displayed_fullness_percent");
                    return new DevicePortBusinessSnapshot(
                            rs.getBoolean("current_bag_present"),
                            rs.getString("baseline_state"),
                            rs.getString("detection_gate"),
                            rs.getString("fullness_state"),
                            fullness == null
                                    ? null
                                    : fullness.stripTrailingZeros()
                                    .toPlainString(),
                            rs.getBoolean("clean_operation_active"));
                },
                tenantCode,
                organizationCode,
                deploymentCode,
                portNo);
        return rows.stream().findFirst()
                .orElse(DevicePortBusinessSnapshot.empty());
    }
}
