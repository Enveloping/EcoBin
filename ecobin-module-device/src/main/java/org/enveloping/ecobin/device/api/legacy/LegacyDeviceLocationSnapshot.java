package org.enveloping.ecobin.device.api.legacy;

import java.math.BigDecimal;

/**
 * operations 旧概览所需的设备位置投影。
 */
public record LegacyDeviceLocationSnapshot(
        LegacyDeviceId id,
        String sn,
        String name,
        BigDecimal lat,
        BigDecimal lng) {
}
