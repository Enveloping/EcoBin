package org.enveloping.ecobin.recycling.application.clean;

import org.enveloping.ecobin.device.api.value.DeviceRuntimeWeightPolicy;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.StartCleanIdentityParticipationPort;
import org.enveloping.ecobin.identity.api.result.LockedCleanOrganizationUser;
import org.enveloping.ecobin.identity.api.result.LockedMiniappCleanScope;
import org.enveloping.ecobin.recycling.web.v1.CleanModels.CleanOperationView;
import org.enveloping.ecobin.recycling.web.v1.CleanModels.CleanOptionsView;
import org.enveloping.ecobin.recycling.web.v1.CleanModels.CleanPortOption;
import org.enveloping.ecobin.recycling.web.v1.CleanModels.RecoverableCleanOperation;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

/** 小程序清运选项和原清运员操作只读视图。 */
@Service
public class CleanQueryService {

    private final JdbcTemplate jdbc;
    private final StartCleanIdentityParticipationPort identity;

    public CleanQueryService(
            JdbcTemplate jdbc,
            StartCleanIdentityParticipationPort identity) {
        this.jdbc = jdbc;
        this.identity = identity;
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public CleanOptionsView options(String deploymentCode) {
        String normalized = deploymentCode(deploymentCode);
        LockedMiniappCleanScope scope =
                identity.lockCurrentMiniappScope();
        ScopeIds ids = scope.organizationScopeRef()
                .withOrganizationScopeOnce(ScopeIds::new);
        LockedCleanOrganizationUser cleaner =
                identity.lockCurrentCleaner(scope);
        return cleaner.organizationUserRef()
                .withOrganizationUserOnce(
                        (tenantId,
                         organizationId,
                         organizationUserId) -> options(
                                ids,
                                tenantId,
                                organizationId,
                                organizationUserId,
                                normalized));
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public CleanOperationView operation(UUID operationUid) {
        if (operationUid == null) {
            throw notFound();
        }
        LockedMiniappCleanScope scope =
                identity.lockCurrentMiniappScope();
        ScopeIds ids = scope.organizationScopeRef()
                .withOrganizationScopeOnce(ScopeIds::new);
        LockedCleanOrganizationUser cleaner =
                identity.lockCurrentCleaner(scope);
        return cleaner.organizationUserRef()
                .withOrganizationUserOnce(
                        (tenantId,
                         organizationId,
                         organizationUserId) -> operation(
                                ids,
                                tenantId,
                                organizationId,
                                organizationUserId,
                                operationUid));
    }

    private CleanOptionsView options(
            ScopeIds locked,
            long tenantId,
            long organizationId,
            long organizationUserId,
            String deploymentCode) {
        requireScope(locked, tenantId, organizationId);
        Deployment deployment = jdbc.query("""
                        SELECT deployment.id, deployment.asset_id,
                               configuration.device_display_name
                                   AS display_name,
                               configuration.location_address AS address
                        FROM dev_device_deployment deployment
                        LEFT JOIN dev_config_version configuration
                          ON configuration.id = (
                              SELECT latest.id
                              FROM dev_config_version latest
                              WHERE latest.tenant_id = deployment.tenant_id
                                AND latest.organization_id = deployment.organization_id
                                AND latest.deployment_id = deployment.id
                              ORDER BY latest.version_no DESC
                              LIMIT 1
                          )
                        WHERE deployment.tenant_id = ?
                          AND deployment.organization_id = ?
                          AND deployment.public_code = ?
                          AND deployment.lifecycle_status = 'ENABLED'
                          AND deployment.business_enabled = 1
                        """,
                (rs, ignored) -> new Deployment(
                        rs.getLong("id"),
                        rs.getLong("asset_id"),
                        rs.getString("display_name"),
                        rs.getString("address")),
                tenantId,
                organizationId,
                deploymentCode).stream().findFirst().orElseThrow(
                CleanQueryService::notFound);
        boolean deviceBusy = Boolean.TRUE.equals(jdbc.queryForObject("""
                        SELECT EXISTS (
                            SELECT 1
                            FROM dev_device_occupancy
                            WHERE asset_id = ?
                        )
                        """,
                Boolean.class,
                deployment.assetId()));
        List<RecoverableCleanOperation> recoverable = jdbc.query("""
                        SELECT operation_uid, port.port_no, status
                        FROM rec_clean_operation operation
                        JOIN dev_port port ON port.id = operation.port_id
                        WHERE operation.tenant_id = ?
                          AND operation.organization_id = ?
                          AND operation.deployment_id = ?
                          AND operation.cleaner_organization_user_id = ?
                          AND operation.status = 'RECOVERY_REQUIRED'
                        ORDER BY operation.id
                        """,
                (rs, ignored) -> {
                    UUID uid = UUID.fromString(
                            rs.getString("operation_uid"));
                    return new RecoverableCleanOperation(
                            uid,
                            rs.getInt("port_no"),
                            rs.getString("status"),
                            "/api/v1/miniapp/clean-operations/" + uid);
                },
                tenantId,
                organizationId,
                deployment.id(),
                organizationUserId);
        List<CleanPortOption> ports = jdbc.query("""
                        SELECT port.port_no,
                               snapshot.display_name,
                               bag.bag_code,
                               capacity.detection_gate,
                               capacity.confirmed_fullness_state,
                               capacity.displayed_fullness_percent,
                               runtime.clean_lock_power_state,
                               runtime.clean_solenoid_health,
                               runtime.weight_sensor_health,
                               runtime.weight_measurement_status,
                               runtime.weight_value_available,
                               runtime.reported_weight_grams,
                               runtime.weight_value_kind,
                               runtime.calibration_version
                                   AS runtime_calibration_version,
                               snapshot.calibration_version
                                   AS configured_calibration_version,
                               runtime.smoke_state,
                               runtime.smoke_sensor_health,
                               runtime.runtime_fault_bitmap,
                               runtime.safety_status,
                               EXISTS (
                                   SELECT 1
                                   FROM rec_clean_operation active_operation
                                   WHERE active_operation.port_id = port.id
                                     AND active_operation.status IN (
                                         'PREPARED', 'EDGE_SAVED',
                                         'IN_PROGRESS', 'RECOVERY_REQUIRED'
                                     )
                               ) AS operation_active
                        FROM dev_port port
                        JOIN dev_config_version configuration
                          ON configuration.id = (
                              SELECT latest.id
                              FROM dev_config_version latest
                              WHERE latest.tenant_id = port.tenant_id
                                AND latest.organization_id = port.organization_id
                                AND latest.deployment_id = port.deployment_id
                              ORDER BY latest.version_no DESC
                              LIMIT 1
                          )
                        JOIN dev_port_config_snapshot snapshot
                          ON snapshot.tenant_id = port.tenant_id
                         AND snapshot.organization_id = port.organization_id
                         AND snapshot.deployment_id = port.deployment_id
                         AND snapshot.config_version_id = configuration.id
                         AND snapshot.port_id = port.id
                        LEFT JOIN dev_port_runtime_state runtime
                          ON runtime.port_id = port.id
                        LEFT JOIN rec_bag_current_occupancy occupancy
                          ON occupancy.port_id = port.id
                         AND occupancy.occupancy_type = 'PORT_BOUND'
                        LEFT JOIN rec_bag bag ON bag.id = occupancy.bag_id
                        LEFT JOIN rec_port_capacity_state capacity
                          ON capacity.port_id = port.id
                        WHERE port.tenant_id = ?
                          AND port.organization_id = ?
                          AND port.deployment_id = ?
                        ORDER BY port.port_no
                        """,
                (rs, ignored) -> portOption(rs, deviceBusy),
                tenantId,
                organizationId,
                deployment.id());
        return new CleanOptionsView(
                deploymentCode,
                deployment.displayName(),
                deployment.address(),
                deviceBusy,
                recoverable,
                databaseNow(),
                ports);
    }

    private CleanOperationView operation(
            ScopeIds locked,
            long tenantId,
            long organizationId,
            long organizationUserId,
            UUID operationUid) {
        requireScope(locked, tenantId, organizationId);
        return jdbc.query("""
                        SELECT operation.operation_uid,
                               operation.status,
                               operation.lock_version,
                               deployment.public_code,
                               port.port_no,
                               operation.old_bag_code_snapshot,
                               operation.new_bag_code_snapshot,
                               operation.first_unlock_may_have_executed,
                               operation.clean_lock_deenergized_confirmed,
                               operation.cleaner_physical_close_confirmed,
                               operation.start_authorization_expires_at,
                               operation.execution_deadline_at,
                               operation.ended_at,
                               record.clean_record_no
                        FROM rec_clean_operation operation
                        JOIN dev_device_deployment deployment
                          ON deployment.id = operation.deployment_id
                        JOIN dev_port port ON port.id = operation.port_id
                        LEFT JOIN rec_clean_record record
                          ON record.id = operation.completion_record_id
                        WHERE operation.tenant_id = ?
                          AND operation.organization_id = ?
                          AND operation.cleaner_organization_user_id = ?
                          AND operation.operation_uid = ?
                        """,
                (rs, ignored) -> operationView(rs),
                tenantId,
                organizationId,
                organizationUserId,
                operationUid.toString())
                .stream()
                .findFirst()
                .orElseThrow(CleanQueryService::notFound);
    }

    private static CleanPortOption portOption(
            ResultSet rs,
            boolean deviceBusy) throws SQLException {
        List<String> blockers = new ArrayList<>();
        if (deviceBusy) {
            blockers.add("DEVICE_BUSY");
        }
        if (rs.getBoolean("operation_active")) {
            blockers.add("CLEAN_OPERATION_ACTIVE");
        }
        if (!"DEENERGIZED".equals(
                rs.getString("clean_lock_power_state"))) {
            blockers.add("CLEAN_LOCK_NOT_SAFE");
        }
        if (!"OK".equals(rs.getString("clean_solenoid_health"))) {
            blockers.add("CLEAN_SOLENOID_UNAVAILABLE");
        }
        if (!DeviceRuntimeWeightPolicy.isStartEligible(
                rs.getString("weight_sensor_health"),
                rs.getString("weight_measurement_status"),
                nullableBoolean(rs, "weight_value_available"),
                nullableLong(rs, "reported_weight_grams"),
                rs.getString("weight_value_kind"),
                nullableLong(rs, "runtime_calibration_version"),
                nullableLong(rs, "configured_calibration_version"))) {
            blockers.add("WEIGHT_UNAVAILABLE");
        }
        if (!"NORMAL".equals(rs.getString("smoke_state"))
                || !"OK".equals(
                rs.getString("smoke_sensor_health"))
                || !"SAFE".equals(rs.getString("safety_status"))) {
            blockers.add("SAFETY_UNAVAILABLE");
        }
        Long faultBitmap = nullableLong(rs, "runtime_fault_bitmap");
        if (faultBitmap == null || faultBitmap != 0L) {
            blockers.add("DEVICE_FAULT_ACTIVE");
        }
        return new CleanPortOption(
                rs.getInt("port_no"),
                rs.getString("display_name"),
                rs.getString("bag_code"),
                fullnessStatus(
                        rs.getString("detection_gate"),
                        rs.getString("confirmed_fullness_state")),
                rs.getBigDecimal("displayed_fullness_percent") == null
                        ? null
                        : rs.getBigDecimal(
                                "displayed_fullness_percent")
                        .toPlainString(),
                blockers.isEmpty(),
                List.copyOf(blockers));
    }

    private static CleanOperationView operationView(ResultSet rs)
            throws SQLException {
        String status = rs.getString("status");
        Long poll = switch (status) {
            case "PREPARED", "EDGE_SAVED", "IN_PROGRESS" -> 1_000L;
            default -> null;
        };
        List<String> actions = switch (status) {
            case "PREPARED", "EDGE_SAVED", "IN_PROGRESS" ->
                    List.of("WAIT");
            case "RECOVERY_REQUIRED" ->
                    List.of("CONTACT_SUPPORT");
            case "COMPLETED" -> List.of("VIEW_RECORD");
            default -> List.of();
        };
        return new CleanOperationView(
                UUID.fromString(rs.getString("operation_uid")),
                status,
                rs.getLong("lock_version"),
                rs.getString("public_code"),
                rs.getInt("port_no"),
                rs.getString("old_bag_code_snapshot"),
                rs.getString("new_bag_code_snapshot"),
                rs.getBoolean("first_unlock_may_have_executed"),
                rs.getBoolean("clean_lock_deenergized_confirmed"),
                rs.getBoolean("cleaner_physical_close_confirmed"),
                instant(rs, "start_authorization_expires_at"),
                nullableInstant(rs, "execution_deadline_at"),
                nullableInstant(rs, "ended_at"),
                rs.getString("clean_record_no"),
                poll,
                actions);
    }

    private Instant databaseNow() {
        LocalDateTime now = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)",
                LocalDateTime.class);
        if (now == null) {
            throw new IllegalStateException(
                    "database time is unavailable");
        }
        return now.toInstant(ZoneOffset.UTC);
    }

    private static String fullnessStatus(
            String gate,
            String confirmed) {
        if ("PENDING".equals(gate) || "IN_PROGRESS".equals(gate)) {
            return "CHECKING";
        }
        if ("FAILED".equals(gate)) {
            return "SOURCE_FAILED";
        }
        if ("READY".equals(gate) && "FULL".equals(confirmed)) {
            return "FULL";
        }
        if ("READY".equals(gate)
                && "NOT_FULL".equals(confirmed)) {
            return "NOT_FULL";
        }
        return "UNKNOWN";
    }

    private static String deploymentCode(String value) {
        if (value == null
                || !value.trim().matches("Dp_[A-Za-z0-9_-]{6,61}")) {
            throw notFound();
        }
        return value.trim();
    }

    private static void requireScope(
            ScopeIds locked,
            long tenantId,
            long organizationId) {
        if (locked.tenantId() != tenantId
                || locked.organizationId() != organizationId) {
            throw new IllegalStateException(
                    "clean query scope changed after identity lock");
        }
    }

    private static Long nullableLong(ResultSet rs, String column)
            throws SQLException {
        long value = rs.getLong(column);
        return rs.wasNull() ? null : value;
    }

    private static Boolean nullableBoolean(ResultSet rs, String column)
            throws SQLException {
        boolean value = rs.getBoolean(column);
        return rs.wasNull() ? null : value;
    }

    private static Instant instant(ResultSet rs, String column)
            throws SQLException {
        return rs.getObject(column, LocalDateTime.class)
                .toInstant(ZoneOffset.UTC);
    }

    private static Instant nullableInstant(
            ResultSet rs,
            String column) throws SQLException {
        LocalDateTime value = rs.getObject(
                column,
                LocalDateTime.class);
        return value == null ? null : value.toInstant(ZoneOffset.UTC);
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404,
                "RESOURCE.NOT_FOUND",
                "资源不存在");
    }

    private record ScopeIds(long tenantId, long organizationId) {
    }

    private record Deployment(
            long id,
            long assetId,
            String displayName,
            String address) {
    }
}
