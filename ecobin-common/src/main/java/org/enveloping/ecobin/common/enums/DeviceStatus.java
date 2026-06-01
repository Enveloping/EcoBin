package org.enveloping.ecobin.common.enums;

import lombok.Getter;

/**
 * 设备状态
 */
@Getter
public enum DeviceStatus {

    OFFLINE(0, "离线"),
    ONLINE(1, "在线"),
    MAINTENANCE(2, "维护中");

    private final int code;
    private final String desc;

    DeviceStatus(int code, String desc) {
        this.code = code;
        this.desc = desc;
    }
}
