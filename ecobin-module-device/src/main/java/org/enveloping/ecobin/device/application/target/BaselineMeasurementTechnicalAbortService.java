package org.enveloping.ecobin.device.application.target;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

/** Ends one baseline generation when no trusted physical result is available. */
@Service
public class BaselineMeasurementTechnicalAbortService {

    private final JdbcTemplate jdbc;

    public BaselineMeasurementTechnicalAbortService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public void abortByCommand(
            UUID commandUid,
            String reasonCode,
            LocalDateTime abortedAt) {
        if (reasonCode == null
                || reasonCode.isBlank()
                || reasonCode.length() > 100) {
            throw new IllegalArgumentException(
                    "baseline technical abort requires a stable reason code");
        }
        List<Target> rows = jdbc.query("""
                        SELECT measurement.id AS measurement_id,
                               measurement.tenant_id,
                               measurement.organization_id,
                               measurement.asset_id,
                               measurement.port_id,
                               measurement.bag_id,
                               factory_bag.id AS factory_bag_id
                        FROM dev_device_command command_row
                        JOIN rec_port_baseline_measurement measurement
                          ON measurement.id =
                              command_row.baseline_measurement_id
                        JOIN dev_port port
                          ON port.id = measurement.port_id
                         AND port.asset_id = measurement.asset_id
                        JOIN rec_bag bag
                          ON bag.id = measurement.bag_id
                         AND bag.tenant_id = measurement.tenant_id
                         AND bag.organization_id =
                             measurement.organization_id
                        LEFT JOIN dev_factory_installed_bag factory_bag
                          ON factory_bag.asset_id = measurement.asset_id
                         AND factory_bag.port_no = port.port_no
                         AND factory_bag.bag_code = bag.bag_code
                        WHERE command_row.command_uid = ?
                          AND command_row.command_type =
                              'MEASURE_EMPTY_BAG_BASELINE'
                        """,
                (rs, ignored) -> new Target(
                        rs.getLong("measurement_id"),
                        rs.getLong("tenant_id"),
                        rs.getLong("organization_id"),
                        rs.getLong("asset_id"),
                        rs.getLong("port_id"),
                        rs.getLong("bag_id"),
                        nullableLong(rs, "factory_bag_id")),
                commandUid.toString());
        if (rows.size() != 1) {
            throw new IllegalStateException(
                    "baseline command target is missing");
        }
        Target target = rows.getFirst();
        List<String> lockedStatuses = jdbc.queryForList("""
                        SELECT status
                        FROM rec_port_baseline_measurement
                        WHERE id = ?
                        FOR UPDATE
                        """,
                String.class,
                target.measurementId());
        if (lockedStatuses.size() != 1) {
            throw new IllegalStateException(
                    "baseline measurement is missing");
        }
        if (!"PENDING".equals(lockedStatuses.getFirst())) {
            return;
        }
        requireSingle(jdbc.update("""
                        UPDATE rec_port_baseline_measurement
                        SET status = 'TECHNICAL_ABORTED',
                            fault_code = ?,
                            completed_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ? AND status = 'PENDING'
                        """,
                reasonCode,
                abortedAt,
                abortedAt,
                target.measurementId()),
                "abort baseline measurement");
        jdbc.update("""
                        UPDATE rec_port_capacity_state
                        SET detection_gate = 'FAILED',
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND port_id = ?
                          AND current_bag_id = ?
                          AND current_baseline_id IS NULL
                        """,
                abortedAt,
                target.tenantId(),
                target.organizationId(),
                target.assetId(),
                target.portId(),
                target.bagId());
        if (target.factoryBagId() != null) {
            requireSingle(jdbc.update("""
                            UPDATE dev_factory_installed_bag
                            SET tare_status = 'FAILED',
                                last_failure_code = ?,
                                updated_at = ?
                            WHERE id = ?
                            """,
                    reasonCode,
                    abortedAt,
                    target.factoryBagId()),
                    "mark factory bag tare failed");
        }
    }

    private static Long nullableLong(
            java.sql.ResultSet resultSet,
            String column) throws java.sql.SQLException {
        long value = resultSet.getLong(column);
        return resultSet.wasNull() ? null : value;
    }

    private static void requireSingle(int updated, String action) {
        if (updated != 1) {
            throw new IllegalStateException(
                    action + " expected one row but updated " + updated);
        }
    }

    private record Target(
            long measurementId,
            long tenantId,
            long organizationId,
            long assetId,
            long portId,
            long bagId,
            Long factoryBagId) {
    }
}
