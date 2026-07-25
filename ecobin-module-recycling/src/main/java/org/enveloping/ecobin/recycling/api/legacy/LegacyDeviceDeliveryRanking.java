package org.enveloping.ecobin.recycling.api.legacy;

import org.enveloping.ecobin.device.api.legacy.LegacyDeviceId;

import java.math.BigDecimal;

/**
 * operations 旧排行所需的设备投递聚合。
 */
public record LegacyDeviceDeliveryRanking(
        LegacyDeviceId deviceId,
        String deviceName,
        BigDecimal totalWeight) {
}
