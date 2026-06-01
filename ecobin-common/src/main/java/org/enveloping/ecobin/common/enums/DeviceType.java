package org.enveloping.ecobin.common.enums;

import lombok.Getter;

/**
 * 设备类型
 */
@Getter
public enum DeviceType {

    GENERAL(0, "通用"),
    SMART_BIN(1, "智能垃圾箱"),
    ROLLING_SYSTEM(2, "滚动系统");

    private final int code;
    private final String desc;

    DeviceType(int code, String desc) {
        this.code = code;
        this.desc = desc;
    }
}
