package org.enveloping.ecobin.device.api.result;

import org.enveloping.ecobin.device.api.persistence.DeliverySessionBusinessQueryRef;

import java.time.Instant;
import java.util.Objects;
import java.util.UUID;

/**
 * 已确认属于当前机构用户的设备投递会话快照。
 */
public record OwnedDeliverySessionSnapshot(
        UUID sessionUid,
        String deviceStatus,
        String deviceCode,
        int portNo,
        Instant firstPhysicalProgressAt,
        Instant deviceCompletedAt,
        Instant endedAt,
        String endReason,
        DeliverySessionBusinessQueryRef businessQueryRef) {

    public OwnedDeliverySessionSnapshot {
        Objects.requireNonNull(sessionUid, "sessionUid");
        if (deviceStatus == null || deviceStatus.isBlank()) {
            throw new IllegalArgumentException(
                    "deviceStatus must not be blank");
        }
        if (deviceCode == null || deviceCode.isBlank()) {
            throw new IllegalArgumentException(
                    "deviceCode must not be blank");
        }
        if (portNo < 1 || portNo > 6) {
            throw new IllegalArgumentException(
                    "portNo must be between 1 and 6");
        }
        Objects.requireNonNull(businessQueryRef, "businessQueryRef");
    }
}
