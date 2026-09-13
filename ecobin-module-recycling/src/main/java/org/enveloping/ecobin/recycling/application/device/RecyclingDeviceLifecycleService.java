package org.enveloping.ecobin.recycling.application.device;

import org.enveloping.ecobin.device.api.port.DeviceLifecycleParticipationPort;
import org.enveloping.ecobin.device.api.persistence.DeviceLifecycleAssetRef;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;

@Service
@Transactional(propagation = Propagation.MANDATORY)
public class RecyclingDeviceLifecycleService implements DeviceLifecycleParticipationPort {
    private final JdbcTemplate jdbc;

    public RecyclingDeviceLifecycleService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public void requireIdle(DeviceLifecycleAssetRef asset) {
        asset.withAssetKeyOnce(this::requireIdle);
    }

    private void requireIdle(long assetId) {
        if (jdbc.queryForObject("""
                SELECT COUNT(*) FROM rec_clean_operation WHERE asset_id = ?
                  AND status NOT IN ('COMPLETED', 'PRE_UNLOCK_ENDED', 'ABORTED')
                """, Long.class, assetId) > 0) {
            throw new TargetApiException(409, "DEVICE.CONTROL_BUSY_CLEAN",
                    "设备仍有清运或待处理的清运结果，结束后才能禁用或报废");
        }
    }

    @Override
    public void cancelDeviceWork(DeviceLifecycleAssetRef asset, String reasonCode, LocalDateTime now) {
        asset.withAssetKeyOnce(assetId -> cancelDeviceWork(assetId, reasonCode, now));
    }

    private void cancelDeviceWork(long assetId, String reasonCode, LocalDateTime now) {
        jdbc.update("""
                UPDATE rec_port_baseline_measurement
                SET status = 'TECHNICAL_ABORTED', fault_code = ?, completed_at = ?,
                    updated_at = ?, lock_version = lock_version + 1
                WHERE asset_id = ? AND status = 'PENDING'
                """, reasonCode, now, now, assetId);
        jdbc.update("""
                UPDATE rec_fullness_detection
                SET status = 'CANCELLED', failure_code = ?, disposition = 'STALE_IGNORED',
                    next_sample_at = NULL, completed_at = ?, updated_at = ?,
                    lock_version = lock_version + 1
                WHERE asset_id = ? AND status IN ('PENDING_INITIAL_SAMPLE', 'WAITING_RECHECK')
                """, reasonCode, now, now, assetId);
        jdbc.update("""
                UPDATE rec_port_capacity_state capacity
                SET detection_gate = 'READY', current_detection_id = NULL,
                    updated_at = ?, lock_version = lock_version + 1
                WHERE asset_id = ? AND current_detection_id IN (
                    SELECT id FROM rec_fullness_detection
                    WHERE asset_id = ? AND status = 'CANCELLED')
                """, now, assetId, assetId);
    }

}
