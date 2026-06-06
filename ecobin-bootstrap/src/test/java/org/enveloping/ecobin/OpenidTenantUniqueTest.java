package org.enveloping.ecobin;

import org.enveloping.ecobin.framework.tenant.TenantContextHolder;
import org.enveloping.ecobin.system.entity.User;
import org.enveloping.ecobin.system.service.UserService;
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
    private UserService userService;

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

    private User wechatUser(Long tenantId, String openid) {
        User user = new User();
        user.setTenantId(tenantId);
        user.setOpenid(openid);
        user.setNickname("测试用户");
        user.setRole(1);
        user.setStatus(1);
        return user;
    }

    @Test
    void sameOpenidCanCoexistAcrossTenants() {
        String openid = "openid-uniq-" + System.nanoTime();

        userService.save(wechatUser(2L, openid));
        User second = wechatUser(3L, openid);
        userService.save(second);

        // 第二个租户下的注册成功，拿到独立主键
        assertNotNull(second.getId());
    }

    @Test
    void sameOpenidRejectedWithinSameTenant() {
        String openid = "openid-dup-" + System.nanoTime();

        userService.save(wechatUser(2L, openid));

        assertThrows(DataIntegrityViolationException.class,
                () -> userService.save(wechatUser(2L, openid)));
    }
}
