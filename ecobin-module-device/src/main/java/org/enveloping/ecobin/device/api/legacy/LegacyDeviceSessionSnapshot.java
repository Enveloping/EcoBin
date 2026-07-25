package org.enveloping.ecobin.device.api.legacy;

/**
 * 旧上传后建单流程读取的活跃会话快照。
 */
public record LegacyDeviceSessionSnapshot(
        Long tenantId,
        Long userId,
        Integer loginType) {
}
