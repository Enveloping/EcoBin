package org.enveloping.ecobin.identity.api.legacy;

/**
 * F-03 前对旧 {@code sys_user.id} 的关系专用包装。
 *
 * <p>它不是目标公开身份，不得进入 HTTP、消息、日志或新业务状态。</p>
 */
public record LegacyOrganizationUserId(long value) {

    public LegacyOrganizationUserId {
        if (value <= 0) {
            throw new IllegalArgumentException("legacy organization user id must be positive");
        }
    }
}
