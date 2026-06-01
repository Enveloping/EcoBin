package org.enveloping.ecobin.common.enums;

import lombok.Getter;

/**
 * 用户角色
 */
@Getter
public enum UserRole {

    SUPER_ADMIN(1, "超级管理员"),
    DEVICE_ADMIN(2, "设备管理员"),
    OPERATOR(3, "运营人员"),
    CLEANER(4, "清运员"),
    NORMAL_USER(5, "普通用户");

    private final int code;
    private final String desc;

    UserRole(int code, String desc) {
        this.code = code;
        this.desc = desc;
    }
}
