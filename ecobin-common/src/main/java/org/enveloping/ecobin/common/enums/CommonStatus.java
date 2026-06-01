package org.enveloping.ecobin.common.enums;

import lombok.Getter;

/**
 * 通用状态
 */
@Getter
public enum CommonStatus {

    DISABLED(0, "禁用"),
    ENABLED(1, "启用");

    private final int code;
    private final String desc;

    CommonStatus(int code, String desc) {
        this.code = code;
        this.desc = desc;
    }
}
