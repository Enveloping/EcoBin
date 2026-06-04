package org.enveloping.ecobin;

import org.enveloping.ecobin.framework.security.TokenInvalidationRegistry;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 强制失效登记表语义校验（纯单元测试，不启动 Spring）。
 */
class TokenInvalidationRegistryTest {

    private static final long DAY_MS = 86_400_000L;

    @Test
    void userTokenInvalidatedUntilReissued() {
        TokenInvalidationRegistry registry = new TokenInvalidationRegistry(DAY_MS);
        long now = System.currentTimeMillis() / 1000;
        long oldIat = now - 100;
        long newIat = now + 100;

        // 变更前：旧 token 有效（用户5 属租户2）
        assertFalse(registry.isInvalidated(1, 5L, 2L, oldIat));

        // 租户更改该用户角色 → 登记失效
        registry.invalidateUser(5L);

        // 旧 token 失效，重新登录后的新 token 恢复有效
        assertTrue(registry.isInvalidated(1, 5L, 2L, oldIat));
        assertFalse(registry.isInvalidated(1, 5L, 2L, newIat));

        // 不影响其他用户
        assertFalse(registry.isInvalidated(1, 6L, 2L, oldIat));
    }

    @Test
    void tenantAndPlatformScoping() {
        TokenInvalidationRegistry registry = new TokenInvalidationRegistry(DAY_MS);
        long oldIat = System.currentTimeMillis() / 1000 - 100;

        registry.invalidateTenant(2L);
        // 租户(7) 命中（userId==tenantId==2）
        assertTrue(registry.isInvalidated(7, 2L, 2L, oldIat));
        // 同 id 的用户(1) key 不同，不受影响
        assertFalse(registry.isInvalidated(1, 2L, 2L, oldIat));
    }

    @Test
    void adminInvalidation() {
        TokenInvalidationRegistry registry = new TokenInvalidationRegistry(DAY_MS);
        long now = System.currentTimeMillis() / 1000;

        // 变更前：管理员 token 有效
        assertFalse(registry.isInvalidated(8, 3L, 1L, now - 100));

        // 超管禁用该管理员 → 强制下线
        registry.invalidateAdmin(3L);
        assertTrue(registry.isInvalidated(8, 3L, 1L, now - 100));
        assertTrue(registry.isInvalidated(9, 3L, 1L, now - 100));
        // 重新登录后的新 token 恢复有效
        assertFalse(registry.isInvalidated(8, 3L, 1L, now + 100));
        // 不影响其他管理员
        assertFalse(registry.isInvalidated(8, 4L, 1L, now - 100));
    }

    @Test
    void disableTenantCascadesToItsUsers() {
        TokenInvalidationRegistry registry = new TokenInvalidationRegistry(DAY_MS);
        long oldIat = System.currentTimeMillis() / 1000 - 100;

        // 禁用租户2 → 连带其名下用户下线
        registry.invalidateTenantUsers(2L);

        // 租户2 下的用户（任意 userId）旧 token 失效
        assertTrue(registry.isInvalidated(1, 50L, 2L, oldIat));
        assertTrue(registry.isInvalidated(2, 51L, 2L, oldIat));
        // 其他租户的用户不受影响
        assertFalse(registry.isInvalidated(1, 50L, 3L, oldIat));
    }
}
