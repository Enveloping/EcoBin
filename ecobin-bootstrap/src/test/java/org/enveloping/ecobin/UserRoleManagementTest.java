package org.enveloping.ecobin;

import org.enveloping.ecobin.common.exception.BusinessException;
import org.enveloping.ecobin.framework.security.JwtTokenProvider;
import org.enveloping.ecobin.framework.security.TokenInvalidationRegistry;
import org.enveloping.ecobin.framework.tenant.TenantContextHolder;
import org.enveloping.ecobin.identity.api.legacy.LegacyOrganizationUserDirectoryPort;
import org.enveloping.ecobin.identity.api.legacy.LegacyOrganizationUserDraft;
import org.enveloping.ecobin.identity.api.legacy.LegacyOrganizationUserId;
import org.enveloping.ecobin.identity.api.legacy.LegacyOrganizationUserUpdate;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 用户角色管理专用端点验证：
 * <ul>
 *   <li>租户提升/降低终端用户角色（限 1/2/3），改后旧 token 立即失效；</li>
 *   <li>拒绝写入平台/租户角色（堵跨域提权洞），通用更新路径同样拦截；</li>
 *   <li>跨租户用户按"不存在"处理；</li>
 *   <li>超管无权调用角色端点（矩阵 §4.1）。</li>
 * </ul>
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
@Transactional
class UserRoleManagementTest {

    @Autowired
    private LegacyOrganizationUserDirectoryPort userDirectory;
    @Autowired
    private TokenInvalidationRegistry tokenInvalidationRegistry;
    @Autowired
    private JwtTokenProvider jwtTokenProvider;
    @Autowired
    private MockMvc mockMvc;

    private final Long tenantId = 2L;
    private Long userId;

    @BeforeEach
    void setUp() {
        TenantContextHolder.setTenantId(tenantId);
        TenantContextHolder.setIgnore(true);
        var user = userDirectory.create(new LegacyOrganizationUserDraft(
                tenantId, null, null, null, null, null,
                "openid-role-" + System.nanoTime(), null, "角色测试用户", null,
                1, 1, null, null));
        userId = user.userId().value();
        TenantContextHolder.clear();
    }

    @AfterEach
    void tearDown() {
        TenantContextHolder.clear();
        SecurityContextHolder.clearContext();
    }

    private void asTenant() {
        SecurityContextHolder.getContext().setAuthentication(
                new UsernamePasswordAuthenticationToken("tenant", tenantId, List.of()));
        TenantContextHolder.setTenantId(tenantId);
        TenantContextHolder.setIgnore(false);
    }

    @Test
    void tenantPromotesUserRole() {
        asTenant();
        var updated = userDirectory.changeRole(new LegacyOrganizationUserId(userId), 2);
        assertEquals(2, updated.role());
        assertEquals(2, userDirectory.find(new LegacyOrganizationUserId(userId)).role());
    }

    @Test
    void changeRoleRejectsPlatformRole() {
        asTenant();
        assertThrows(BusinessException.class,
                () -> userDirectory.changeRole(new LegacyOrganizationUserId(userId), 9));
        // 库中角色未被改动
        assertEquals(1, userDirectory.find(new LegacyOrganizationUserId(userId)).role());
    }

    @Test
    void changeRoleInvalidatesUserToken() {
        asTenant();
        userDirectory.changeRole(new LegacyOrganizationUserId(userId), 3);
        // 以"过去的签发时刻"断言旧 token 已失效
        long pastIat = System.currentTimeMillis() / 1000 - 100;
        assertTrue(tokenInvalidationRegistry.isInvalidated(3, userId, tenantId, pastIat));
    }

    @Test
    void crossTenantUserNotFound() {
        // 在租户 3 下另造一个用户
        TenantContextHolder.setTenantId(3L);
        TenantContextHolder.setIgnore(true);
        var other = userDirectory.create(new LegacyOrganizationUserDraft(
                3L, null, null, null, null, null,
                "openid-role-other-" + System.nanoTime(), null, "他租户用户", null,
                1, 1, null, null));
        Long otherId = other.userId().value();
        TenantContextHolder.clear();

        asTenant();   // 租户 2 上下文
        assertThrows(BusinessException.class,
                () -> userDirectory.changeRole(new LegacyOrganizationUserId(otherId), 2));
    }

    @Test
    void genericUpdateRejectsPlatformRole() {
        asTenant();
        var update = new LegacyOrganizationUserUpdate(
                new LegacyOrganizationUserId(userId), null, null, null, null,
                null, null, 8, null);
        assertThrows(BusinessException.class, () -> userDirectory.update(update));
    }

    @Test
    void superAdminForbiddenFromRoleEndpoint() throws Exception {
        // 超管 token：role=9，平台池 tenantId=1
        String adminToken = jwtTokenProvider.generateToken(1L, "admin", 1L, 9);
        mockMvc.perform(put("/api/system/user/" + userId + "/role")
                        .header("Authorization", "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"role\":2}"))
                .andExpect(status().isForbidden());
    }
}
