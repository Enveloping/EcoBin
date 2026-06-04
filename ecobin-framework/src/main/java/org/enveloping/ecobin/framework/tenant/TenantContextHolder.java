package org.enveloping.ecobin.framework.tenant;

/**
 * 租户上下文持有者（基于 ThreadLocal）
 * <p>
 * 当前单租户阶段默认使用 tenant_id = 1，
 * 多租户阶段从 JWT Token 或请求头解析实际租户 ID。
 */
public final class TenantContextHolder {

    private TenantContextHolder() {
    }

    private static final ThreadLocal<Long> TENANT_CONTEXT = new ThreadLocal<>();

    /** 是否忽略租户过滤（平台域 / 登录前流程）。 */
    private static final ThreadLocal<Boolean> IGNORE_CONTEXT = new ThreadLocal<>();

    /**
     * 设置当前线程的租户ID
     */
    public static void setTenantId(Long tenantId) {
        TENANT_CONTEXT.set(tenantId);
    }

    /**
     * 获取当前线程的租户ID
     */
    public static Long getTenantId() {
        return TENANT_CONTEXT.get();
    }

    /**
     * 设置是否忽略租户过滤（true 时 MyBatis 不注入 tenant_id 条件）。
     */
    public static void setIgnore(boolean ignore) {
        IGNORE_CONTEXT.set(ignore);
    }

    /**
     * 当前线程是否忽略租户过滤（默认 false）。
     */
    public static boolean isIgnore() {
        return Boolean.TRUE.equals(IGNORE_CONTEXT.get());
    }

    /**
     * 清理 ThreadLocal（租户ID + 忽略标志）
     */
    public static void clear() {
        TENANT_CONTEXT.remove();
        IGNORE_CONTEXT.remove();
    }
}
