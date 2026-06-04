package org.enveloping.ecobin.common.constant;

/**
 * 系统常量
 */
public final class Constants {

    private Constants() {
    }

    /** 平台池租户ID（保留值，表示未分配；平台域主体上下文也用此值） */
    public static final long DEFAULT_TENANT_ID = 1L;

    /** 平台池租户ID 别名（语义更清晰） */
    public static final long PLATFORM_POOL_TENANT_ID = 1L;

    /** 默认分页大小 */
    public static final int DEFAULT_PAGE_SIZE = 20;

    /** 最大分页大小 */
    public static final int MAX_PAGE_SIZE = 200;

    /** JWT Token 请求头 */
    public static final String TOKEN_HEADER = "Authorization";

    /** JWT Token 前缀 */
    public static final String TOKEN_PREFIX = "Bearer ";

    /** 租户ID请求头 */
    public static final String TENANT_HEADER = "X-Tenant-Id";

    /** 微信用户默认昵称前缀 */
    public static final String WECHAT_NICKNAME_PREFIX = "微信用户";

    /** 微信用户默认角色（普通用户，新角色体系 role=1） */
    public static final int WECHAT_DEFAULT_ROLE = 1;
}
