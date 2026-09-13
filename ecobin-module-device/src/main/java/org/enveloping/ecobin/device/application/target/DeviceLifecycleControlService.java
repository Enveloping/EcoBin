package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.port.DeviceLifecycleParticipationPort;
import org.enveloping.ecobin.device.api.port.UnsentUpgradeAuthorizationPort;
import org.enveloping.ecobin.device.application.software.DeviceSoftwareCompatibilityService;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskLifecyclePort;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

/** Checks and closes cloud work without inventing physical completion facts. */
@Service
public class DeviceLifecycleControlService implements UnsentUpgradeAuthorizationPort {
    private final JdbcTemplate jdbc;
    private final ReliableDeviceTaskLifecyclePort tasks;
    private final List<DeviceLifecycleParticipationPort> participants;
    private final DeviceSoftwareCompatibilityService compatibility;

    public DeviceLifecycleControlService(JdbcTemplate jdbc,
            ReliableDeviceTaskLifecyclePort tasks,
            List<DeviceLifecycleParticipationPort> participants,
            DeviceSoftwareCompatibilityService compatibility) {
        this.jdbc = jdbc;
        this.tasks = tasks;
        this.participants = List.copyOf(participants);
        this.compatibility = compatibility;
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public void requireIdle(long assetId, String hardwareSn) {
        if (exists("SELECT COUNT(*) FROM dev_delivery_session WHERE asset_id = ? "
                + "AND status NOT IN ('BUSINESS_CONFIRMED', 'PRE_OPEN_ENDED', 'DEVICE_ABORTED')", assetId)) {
            throw busy("DELIVERY", "设备仍有投递或待处理的投递结果，结束后才能禁用或报废");
        }
        participants.forEach(participant -> participant.requireIdle(new LockedDeviceLifecycleAssetRef(assetId)));
        if (exists("SELECT COUNT(*) FROM dev_device_occupancy WHERE asset_id = ?", assetId)) {
            throw busy("OCCUPIED", "设备仍被投递或清运占用，处理完成后才能禁用或报废");
        }
        if (exists("""
                SELECT COUNT(*) FROM dev_remote_support_session WHERE asset_id = ?
                  AND (state NOT IN ('CLOSED', 'FAILED', 'EXPIRED')
                    OR lease_released_at IS NULL OR server_lease_state IN ('DESIRED', 'ACTIVE', 'ERROR'))
                """, assetId)) {
            throw busy("REMOTE_SUPPORT", "远程维护尚未完全关闭，请先结束维护再禁用或报废");
        }
        for (var row : jdbc.queryForList("""
                SELECT authorization_status, reliable_task_uid FROM dev_factory_seal_authorization
                WHERE asset_id = ? AND authorization_status IN ('PENDING', 'ACKNOWLEDGED')
                """, assetId)) {
            if ("ACKNOWLEDGED".equals(row.get("authorization_status"))
                    || tasks.externalCallMayHaveStarted(UUID.fromString(row.get("reliable_task_uid").toString()))) {
                throw busy("FACTORY_SEAL", "设备已收到出厂封存授权，请等待封存结束后再禁用或报废");
            }
        }
        requireUpgradeIdle(assetId, "dev_mcu_firmware_deployment",
                "'SUCCEEDED', 'ROLLED_BACK', 'FAILED_LOCKED', 'REJECTED', 'LOCAL_CANCELLED'",
                List.of("PENDING", "QUEUED"));
        requireUpgradeIdle(assetId, "dev_edge_software_deployment",
                "'SUCCEEDED', 'ROLLED_BACK', 'DEFERRED', 'REJECTED', 'FAILED_LOCKED', 'CANCELLED', 'LOCAL_CANCELLED'",
                List.of("PLANNED", "QUEUED"));
    }

    private void requireUpgradeIdle(long assetId, String table, String terminal, List<String> unstarted) {
        // Table names and status lists are internal constants, never request data.
        for (var row : jdbc.queryForList("SELECT deployment_status, reliable_task_uid FROM " + table
                + " WHERE asset_id = ? AND deployment_status NOT IN (" + terminal + ")", assetId)) {
            Object taskUid = row.get("reliable_task_uid");
            if (!unstarted.contains(row.get("deployment_status"))
                    || (taskUid != null && tasks.externalCallMayHaveStarted(UUID.fromString(taskUid.toString())))) {
                throw busy("UPGRADE", "设备升级已下发，请等待升级结束或安全取消确认后再禁用或报废");
            }
        }
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public void cancelDeviceWork(long assetId, String hardwareSn, String targetStatus, LocalDateTime now) {
        String reasonCode = "DEVICE_" + targetStatus;
        boolean retiring = "RETIRED".equals(targetStatus);
        if (retiring) tasks.cancelDeviceWork(hardwareSn);
        else tasks.pauseDeviceWork(hardwareSn);
        jdbc.update("""
                UPDATE dev_factory_seal_authorization
                SET authorization_status = 'CANCELLED', cancelled_at = ?, cancellation_reason = ?,
                    updated_at = ?
                WHERE asset_id = ? AND authorization_status = 'PENDING'
                """, now, reasonCode, now, assetId);
        jdbc.update("""
                UPDATE dev_device_asset SET acceptance_status = 'PENDING', accepted_at = NULL,
                    acceptance_evidence_sha256 = NULL, acceptance_failure_json = NULL
                WHERE id = ? AND tenant_id IS NULL AND acceptance_status = 'PASSED'
                  AND EXISTS (SELECT 1 FROM dev_factory_seal_authorization seal_row
                    WHERE seal_row.asset_id = dev_device_asset.id
                      AND seal_row.acceptance_generation = dev_device_asset.acceptance_generation
                      AND seal_row.authorization_status = 'CANCELLED'
                      AND seal_row.cancellation_reason IN ('DEVICE_DISABLED', 'DEVICE_RETIRED'))
                """, assetId);
        if (retiring) {
            jdbc.update("""
                    UPDATE dev_config_application SET status = 'CANCELLED', last_failure_code = ?,
                        last_failure_at = ?, updated_at = ?, lock_version = lock_version + 1
                    WHERE asset_id = ? AND status IN ('PENDING', 'EDGE_SAVED')
                    """, reasonCode, now, now, assetId);
            for (String table : List.of("dev_mcu_firmware_deployment", "dev_edge_software_deployment")) {
                int cancelled = jdbc.update("UPDATE " + table + " SET deployment_status = 'LOCAL_CANCELLED', error_code = ?, "
                        + "completed_at = ?, updated_at = ?, lock_version = lock_version + 1 "
                        + "WHERE asset_id = ? AND deployment_status IN ('PENDING', 'PLANNED', 'QUEUED')",
                        reasonCode, now, now, assetId);
                if (cancelled > 0 && "dev_edge_software_deployment".equals(table)) {
                    compatibility.reassessLatestFact(assetId, now);
                }
            }
        }
        participants.forEach(participant -> participant.cancelDeviceWork(new LockedDeviceLifecycleAssetRef(assetId), reasonCode, now));
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public void resumeDeviceWork(long assetId, String hardwareSn, LocalDateTime now) {
        for (String table : List.of("dev_mcu_firmware_deployment", "dev_edge_software_deployment")) {
            for (String oldTask : jdbc.queryForList("SELECT reliable_task_uid FROM " + table
                    + " WHERE asset_id = ? AND deployment_status = 'QUEUED'", String.class, assetId)) {
                tasks.renewExpiredUnsentUpgradeTask(hardwareSn, UUID.fromString(oldTask)).ifPresent(renewed -> {
                    int changed = jdbc.update("UPDATE " + table + " SET command_uid = ?, reliable_task_uid = ?, "
                            + "queued_at = ?, updated_at = ?, lock_version = lock_version + 1 "
                            + "WHERE asset_id = ? AND reliable_task_uid = ? AND deployment_status = 'QUEUED'",
                            renewed.commandUid().toString(), renewed.taskUid().toString(), now, now, assetId, oldTask);
                    if (changed != 1) throw new IllegalStateException("upgrade authorization renewal lost its deployment");
                });
            }
        }
        tasks.resumeDeviceWork(hardwareSn);
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public boolean refreshBeforeDispatch(String hardwareSn, UUID taskUid) {
        long assetId = jdbc.queryForObject("SELECT id FROM dev_device_asset WHERE hardware_sn = ? FOR UPDATE",
                Long.class, hardwareSn);
        for (String table : List.of("dev_mcu_firmware_deployment", "dev_edge_software_deployment")) {
            if (jdbc.queryForObject("SELECT COUNT(*) FROM " + table + " WHERE asset_id = ? "
                    + "AND deployment_status = 'QUEUED' AND reliable_task_uid = ?",
                    Long.class, assetId, taskUid.toString()) == 0) continue;
            var renewed = tasks.renewExpiredUnsentUpgradeTask(hardwareSn, taskUid);
            if (renewed.isEmpty()) return false;
            var next = renewed.get();
            int changed = jdbc.update("UPDATE " + table + " SET command_uid = ?, reliable_task_uid = ?, "
                    + "queued_at = UTC_TIMESTAMP(3), updated_at = UTC_TIMESTAMP(3), lock_version = lock_version + 1 "
                    + "WHERE asset_id = ? AND reliable_task_uid = ? AND deployment_status = 'QUEUED'",
                    next.commandUid().toString(), next.taskUid().toString(), assetId, taskUid.toString());
            if (changed != 1) throw new IllegalStateException("upgrade authorization renewal lost its deployment");
            return true;
        }
        return false;
    }

    private boolean exists(String sql, long assetId) {
        return jdbc.queryForObject(sql, Long.class, assetId) > 0;
    }

    private static TargetApiException busy(String kind, String message) {
        return new TargetApiException(409, "DEVICE.CONTROL_BUSY_" + kind, message);
    }
}
