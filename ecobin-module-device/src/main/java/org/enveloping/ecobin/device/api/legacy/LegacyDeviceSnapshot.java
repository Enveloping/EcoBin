package org.enveloping.ecobin.device.api.legacy;

/**
 * recycling 旧流程所需的最小设备快照，不暴露 device Entity。
 */
public record LegacyDeviceSnapshot(
        LegacyDeviceId id,
        Long tenantId,
        String sn,
        String name) {
}
