package org.enveloping.ecobin.device.api.legacy;

import java.util.Objects;

/**
 * 旧 V1～V14 设备主键的边界包装。仅用于 F-03 保持旧行为的同步模块端口。
 */
public record LegacyDeviceId(Long value) {

    public LegacyDeviceId {
        Objects.requireNonNull(value, "device id");
    }
}
