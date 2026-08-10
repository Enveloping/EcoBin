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
            String deviceCode,
            int portNo) {
        List<DevicePortBusinessSnapshot> rows = jdbc.query("""
                        SELECT
                            CASE WHEN b.port_id IS NULL THEN 0 ELSE 1 END
                                AS current_bag_present,
                            CASE
                                WHEN c.baseline_state = 'VALID'
                                 AND c.current_bag_id = b.bag_id
                                 AND c.current_baseline_id = baseline.id
                                 AND baseline.baseline_weight_g =
                                     c.current_baseline_weight_g
                                 AND baseline.bag_id = b.bag_id
                                THEN 'VALID'
                                WHEN c.baseline_state = 'VALID'
                                THEN 'INVALID'
                                ELSE COALESCE(
                                    c.baseline_state, 'UNINITIALIZED')
                            END AS baseline_state,
                            CASE
                                WHEN c.current_bag_id = b.bag_id
                                THEN COALESCE(c.detection_gate, 'UNKNOWN')
                                ELSE 'UNKNOWN'
                            END AS detection_gate,
                            CASE
                                WHEN c.current_bag_id = b.bag_id
                                THEN COALESCE(
                                    c.confirmed_fullness_state, 'UNKNOWN')
                                ELSE 'UNKNOWN'
                            END AS fullness_state,
                            CASE
                                WHEN c.current_bag_id = b.bag_id
                                THEN c.displayed_fullness_percent
                                ELSE NULL
                            END AS displayed_fullness_percent,
                            EXISTS (
                                SELECT 1
                                FROM rec_clean_operation clean
                                WHERE clean.tenant_id = a.tenant_id
                                  AND clean.organization_id =
                                      a.organization_id
                                  AND clean.asset_id = a.id
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
                        JOIN dev_device_asset a
                          ON a.tenant_id = t.id
                         AND a.organization_id = o.id
                        JOIN dev_port p
                          ON p.tenant_id = a.tenant_id
                         AND p.organization_id = a.organization_id
                         AND p.asset_id = a.id
                        LEFT JOIN rec_bag_current_occupancy b
                          ON b.tenant_id = p.tenant_id
                         AND b.organization_id = p.organization_id
                         AND b.port_id = p.id
                         AND b.occupancy_type = 'PORT_BOUND'
                        LEFT JOIN rec_port_capacity_state c
                          ON c.tenant_id = p.tenant_id
                         AND c.organization_id = p.organization_id
                         AND c.asset_id = p.asset_id
                         AND c.port_id = p.id
                        LEFT JOIN rec_port_weight_baseline baseline
                          ON baseline.tenant_id = c.tenant_id
                         AND baseline.organization_id = c.organization_id
                         AND baseline.port_id = c.port_id
                         AND baseline.id = c.current_baseline_id
                        WHERE t.tenant_code = ?
                          AND o.organization_code = ?
                          AND a.device_public_code = ?
                          AND a.lifecycle_status = 'NORMAL'
                          AND a.acceptance_status = 'PASSED'
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
                deviceCode,
                portNo);
        return rows.stream().findFirst()
                .orElse(DevicePortBusinessSnapshot.empty());
    }
}
