package org.enveloping.ecobin.framework.security;

import org.enveloping.ecobin.common.enums.UserRole;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Token 强制失效登记表。
 * <p>
 * 当管理员/租户/用户的角色或状态被变更时，记录其标识符与变更时刻（秒）。JWT 校验时比对：
 * 若 token 的签发时间早于记录的失效时刻，则该 token 视为失效，需重新登录；重新登录后签发的新 token
 * 签发时间晚于失效时刻，自动恢复有效。
 * <p>
 * 登记维度：
 * <ul>
 *   <li>{@code admin:{id}} —— 管理员被改角色/状态（如超管禁用其他管理员）</li>
 *   <li>{@code tenant:{id}} —— 租户被改状态</li>
 *   <li>{@code user:{id}} —— 终端用户被改角色/状态</li>
 *   <li>{@code tenantUsers:{tenantId}} —— 租户被禁用时连带其名下全部用户下线</li>
 * </ul>
 * 当前为单实例内存实现（{@link ConcurrentHashMap}）；多实例部署时应替换为 Redis 等共享存储。
 */
@Component
public class TokenInvalidationRegistry {

    /** key -> 失效时刻（epoch 秒） */
    private final Map<String, Long> invalidatedAt = new ConcurrentHashMap<>();

    /** 登记保留时长（秒），等于 JWT 有效期；超过此时长的旧记录对应 token 已自然过期，可清理。 */
    private final long ttlSeconds;

    public TokenInvalidationRegistry(@Value("${jwt.expiration:86400000}") long expirationMs) {
        this.ttlSeconds = expirationMs / 1000;
    }

    /** 登记某管理员需强制重新登录 */
    public void invalidateAdmin(Long adminId) {
        record("admin:" + adminId);
    }

    /** 登记某租户需强制重新登录 */
    public void invalidateTenant(Long tenantId) {
        record("tenant:" + tenantId);
    }

    /** 登记某终端用户需强制重新登录 */
    public void invalidateUser(Long userId) {
        record("user:" + userId);
    }

    /** 登记某租户名下全部用户需强制重新登录（租户被禁用时连带下线） */
    public void invalidateTenantUsers(Long tenantId) {
        record("tenantUsers:" + tenantId);
    }

    /**
     * 校验 token 是否已被强制失效。
     *
     * @param role        token 中的角色
     * @param userId      token 主体ID（adminId / tenantId / userId）
     * @param tenantId    token 所属租户ID（终端用户连带下线、租户自身校验用）
     * @param tokenIatSec token 签发时间（epoch 秒）
     * @return true 表示已失效，应拒绝
     */
    public boolean isInvalidated(Integer role, Long userId, Long tenantId, long tokenIatSec) {
        if (role == null) {
            return false;
        }
        if (role >= UserRole.ADMIN.getCode()) {
            return after("admin:" + userId, tokenIatSec);
        }
        if (role == UserRole.TENANT.getCode()) {
            return after("tenant:" + tenantId, tokenIatSec);
        }
        // 终端用户：自身被变更，或其所属租户被禁用而连带下线
        return after("user:" + userId, tokenIatSec)
                || after("tenantUsers:" + tenantId, tokenIatSec);
    }

    private boolean after(String key, long tokenIatSec) {
        Long invalidated = invalidatedAt.get(key);
        return invalidated != null && tokenIatSec < invalidated;
    }

    private void record(String key) {
        long now = System.currentTimeMillis() / 1000;
        invalidatedAt.put(key, now);
        // 顺带清理已过期的旧记录
        invalidatedAt.values().removeIf(t -> t < now - ttlSeconds);
    }
}
