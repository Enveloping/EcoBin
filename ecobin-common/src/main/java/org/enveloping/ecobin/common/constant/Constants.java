package org.enveloping.ecobin.common.constant;

/**
 * 系统常量
 */
public final class Constants {

    private Constants() {
    }

    /** 默认租户ID（单租户阶段固定） */
    public static final long DEFAULT_TENANT_ID = 1L;

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
}
