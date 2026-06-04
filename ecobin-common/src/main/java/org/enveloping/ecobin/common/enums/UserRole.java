package org.enveloping.ecobin.common.enums;

import lombok.Getter;

/**
 * 角色体系（硬编码，跨三张登录主体表）。
 * <p>
 * 角色不是线性高低，而是分属三个作用域：
 * 平台域（9/8，sys_admin）、租户域（7，sys_tenant）、终端域（3/2/1，sys_user）。
 * 详见 docs/permission-design.md。
 */
@Getter
public enum UserRole {

    SUPER_ADMIN(9, "超级管理员"),
    ADMIN(8, "管理员"),
    TENANT(7, "租户"),
    DEVICE_ADMIN(3, "设备管理员"),
    CLEANER(2, "清运员"),
    USER(1, "普通用户");

    private final int code;
    private final String desc;

    UserRole(int code, String desc) {
        this.code = code;
        this.desc = desc;
    }

    /**
     * 由 role 值得到 Spring Security 角色名（不含 ROLE_ 前缀）。
     * 未知 role 返回 null。
     */
    public static String authorityOf(Integer code) {
        if (code == null) {
            return null;
        }
        for (UserRole r : values()) {
            if (r.code == code) {
                return r.name();
            }
        }
        return null;
    }

    /** 是否平台域角色（超管/管理员），TenantInterceptor 对其放行。 */
    public static boolean isPlatform(Integer code) {
        return code != null && code >= ADMIN.code;
    }
}
