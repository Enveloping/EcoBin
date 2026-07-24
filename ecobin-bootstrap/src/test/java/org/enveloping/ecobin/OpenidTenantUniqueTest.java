package org.enveloping.ecobin;

import org.enveloping.ecobin.framework.tenant.TenantContextHolder;
import org.enveloping.ecobin.identity.api.legacy.LegacyOrganizationUserDirectoryPort;
import org.enveloping.ecobin.identity.api.legacy.LegacyOrganizationUserDraft;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.transaction.annotation.Transactional;

import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

/**
 * 验证 V6 后 openid 的唯一约束语义：复合唯一 {@code (tenant_id, openid)}。
 * <ul>
 *   <li>同一微信 openid 在不同租户下可独立注册（多租户设计要求）；</li>
 *   <li>同一租户下相同 openid 仍被唯一约束拒绝。</li>
 * </ul>
 * 走 {@code UserService.save}，与 wx-login 自动注册路径一致。事务回滚，不污染其它用例。
 */
@SpringBootTest
@ActiveProfiles("test")
@Transactional
class OpenidTenantUniqueTest {

    @Autowired
    private LegacyOrganizationUserDirectoryPort userDirectory;

    @BeforeEach
    void setUp() {
        // 平台域放行租户拦截器，save 时使用实体上显式设置的 tenant_id
        TenantContextHolder.setTenantId(1L);
        TenantContextHolder.setIgnore(true);
    }

    @AfterEach
    void tearDown() {
        TenantContextHolder.clear();
    }

    private LegacyOrganizationUserDraft wechatUser(Long tenantId, String openid) {
        return new LegacyOrganizationUserDraft(
                tenantId, null, null, null, null, null, openid, null,
                "测试用户", null, 1, 1, null, null);
    }

    @Test
    void sameOpenidCanCoexistAcrossTenants() {
        String openid = "openid-uniq-" + System.nanoTime();

        userDirectory.create(wechatUser(2L, openid));
        var second = userDirectory.create(wechatUser(3L, openid));

        // 第二个租户下的注册成功，拿到独立主键
        assertNotNull(second.userId());
    }

    @Test
    void sameOpenidRejectedWithinSameTenant() {
        String openid = "openid-dup-" + System.nanoTime();

        userDirectory.create(wechatUser(2L, openid));

        assertThrows(DataIntegrityViolationException.class,
                () -> userDirectory.create(wechatUser(2L, openid)));
    }
}
