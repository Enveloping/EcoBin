package org.enveloping.ecobin.recycling.application.device;

import org.enveloping.ecobin.device.api.port.TrustedEdgeRestartedBusinessPort;
import org.enveloping.ecobin.device.api.result.TrustedEdgeRestartedWork;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

/**
 * Closes recycling-owned physical work after a trusted EDGE_RESTARTED fact.
 */
@Service
public class AbortEdgeRestartedWorkService
        implements TrustedEdgeRestartedBusinessPort {

    private final JdbcTemplate jdbc;

    public AbortEdgeRestartedWorkService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public void abortRestartedWork(TrustedEdgeRestartedWork work) {
        switch (work.commandType()) {
            case "START_CLEAN_OPERATION",
                 "END_CLEAN_BEFORE_UNLOCK",
                 "RESUME_CLEAN_OPERATION" -> abortClean(work);
            case "SAMPLE_FULLNESS" -> abortFullness(work);
            case "MEASURE_EMPTY_BAG_BASELINE" -> abortBaseline(work);
            default -> {
                // Delivery is device-owned; configuration is restart-safe.
            }
        }
    }

    private void abortClean(TrustedEdgeRestartedWork work) {
        if (work.cleanOperationId() == null) {
            return;
        }
        List<CleanTarget> rows = jdbc.query("""
                        SELECT id, port_id, status
                        FROM rec_clean_operation
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new CleanTarget(
                        rs.getLong("id"),
                        rs.getLong("port_id"),
                        rs.getString("status")),
                work.cleanOperationId(),
                work.tenantId(),
                work.organizationId(),
                work.assetId());
        if (rows.size() != 1) {
            return;
        }
        CleanTarget target = rows.getFirst();
        if (List.of("COMPLETED", "PRE_UNLOCK_ENDED", "ABORTED")
                .contains(target.status())) {
            return;
        }
        int updated = jdbc.update("""
                        UPDATE rec_clean_operation
                        SET status = 'ABORTED',
                            ended_at = ?,
                            end_reason = 'EDGE_RESTARTED',
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND status IN (
                              'PREPARED', 'EDGE_SAVED',
                              'IN_PROGRESS', 'RECOVERY_REQUIRED'
                          )
                        """,
                work.observedAt(),
                work.observedAt(),
                target.id(),
                work.tenantId(),
                work.organizationId());
        if (updated != 1) {
            return;
        }
        // Native Pi restarts keep their permanent work permit and do not
        // emit this legacy terminal fact. If an older edge does emit it,
        // preserve both occupancies so the same two-bag manual recovery can
        // reconcile the physical bag instead of creating another dead lock.
        jdbc.update("""
                        UPDATE rec_clean_photo
                        SET status = 'PERMANENTLY_MISSING',
                            linked_at = ?,
                            missing_reason = 'EDGE_RESTARTED',
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND clean_operation_id = ?
                          AND status = 'UPLOAD_PENDING'
                        """,
                work.observedAt(),
                work.observedAt(),
                work.tenantId(),
                work.organizationId(),
                target.id());
        jdbc.update("""
                        INSERT INTO rec_port_clean_restart_interlock (
                            tenant_id, organization_id,
                            asset_id, port_id,
                            source_clean_operation_id,
                            activated_at, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                work.tenantId(),
                work.organizationId(),
                work.assetId(),
                target.portId(),
                target.id(),
                work.observedAt(),
                work.observedAt(),
                work.observedAt());
    }

    private void abortBaseline(TrustedEdgeRestartedWork work) {
        if (work.baselineMeasurementId() == null) {
            return;
        }
        List<BaselineTarget> rows = jdbc.query("""
                        SELECT clean_bag_recovery_id, status
                        FROM rec_port_baseline_measurement
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new BaselineTarget(
                        nullableLong(rs, "clean_bag_recovery_id"),
                        rs.getString("status")),
                work.baselineMeasurementId(),
                work.tenantId(),
                work.organizationId(),
                work.assetId());
        if (rows.size() != 1 || !"PENDING".equals(rows.getFirst().status())) {
            return;
        }
        requireSingle(jdbc.update("""
                        UPDATE rec_port_baseline_measurement
                        SET status = 'FAILED',
                            physical_result_id = NULL,
                            stable_total_weight_g = NULL,
                            fault_code = 'EDGE_RESTARTED',
                            result_baseline_id = NULL,
                            completed_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND status = 'PENDING'
                        """,
                work.observedAt(),
                work.observedAt(),
                work.baselineMeasurementId(),
                work.tenantId(),
                work.organizationId(),
                work.assetId()),
                "abort restarted baseline measurement");
        if (rows.getFirst().cleanBagRecoveryId() != null) {
            requireSingle(jdbc.update("""
                            UPDATE rec_clean_bag_recovery
                            SET status = 'BASELINE_REQUIRED',
                                lock_version = lock_version + 1,
                                updated_at = ?
                            WHERE id = ?
                              AND tenant_id = ?
                              AND organization_id = ?
                              AND status = 'BASELINE_PENDING'
                            """,
                    work.observedAt(),
                    rows.getFirst().cleanBagRecoveryId(),
                    work.tenantId(),
                    work.organizationId()),
                    "return restarted clean bag recovery to baseline required");
        }
    }

    private void abortFullness(TrustedEdgeRestartedWork work) {
        if (work.fullnessDetectionId() == null) {
            return;
        }
        List<Long> ports = jdbc.query("""
                        SELECT port_id
                        FROM rec_fullness_detection
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("port_id"),
                work.fullnessDetectionId(),
                work.tenantId(),
                work.organizationId(),
                work.assetId());
        if (ports.size() != 1) {
            return;
        }
        int updated = jdbc.update("""
                        UPDATE rec_fullness_detection
                        SET status = 'FAILED',
                            final_result = 'SOURCE_FAILED',
                            failure_code = 'EDGE_RESTARTED',
                            disposition = 'APPLIED',
                            initial_sample_id = NULL,
                            initial_sample_conclusion = NULL,
                            terminal_sample_id = NULL,
                            terminal_sample_conclusion = NULL,
                            next_sample_at = NULL,
                            completed_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND status IN (
                              'PENDING_INITIAL_SAMPLE',
                              'WAITING_RECHECK'
                          )
                        """,
                work.observedAt(),
                work.observedAt(),
                work.fullnessDetectionId(),
                work.tenantId(),
                work.organizationId());
        if (updated == 1) {
            jdbc.update("""
                            UPDATE rec_port_capacity_state
                            SET detection_gate = 'READY',
                                current_detection_id = NULL,
                                lock_version = lock_version + 1,
                                updated_at = ?
                            WHERE tenant_id = ?
                              AND organization_id = ?
                              AND asset_id = ?
                              AND port_id = ?
                              AND current_detection_id = ?
                            """,
                    work.observedAt(),
                    work.tenantId(),
                    work.organizationId(),
                    work.assetId(),
                    ports.getFirst(),
                    work.fullnessDetectionId());
        }
    }

    private record CleanTarget(
            long id,
            long portId,
            String status) {
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

    private record BaselineTarget(
            Long cleanBagRecoveryId,
            String status) {
    }
}
