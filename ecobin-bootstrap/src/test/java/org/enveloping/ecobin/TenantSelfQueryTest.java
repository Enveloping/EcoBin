package org.enveloping.ecobin;

import org.enveloping.ecobin.framework.security.JwtTokenProvider;
import org.enveloping.ecobin.framework.tenant.TenantContextHolder;
import org.enveloping.ecobin.system.entity.Tenant;
import org.enveloping.ecobin.system.service.TenantService;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 租户自查端点的 HTTP 层权限端到端验证（MockMvc + 真实 JWT，走完整 Spring Security 过滤链）：
 * <ul>
 *   <li>租户（role=7）持有效 token 可访问 {@code GET /api/system/tenant/me} 查看自身资料；</li>
 *   <li>响应不泄露 password / miniappSecret（已 {@code @JsonProperty(WRITE_ONLY)} 脱敏）；</li>
 *   <li>租户访问全量列表 {@code GET /api/system/tenant} 被 403 拒绝（仅超管/管理员可见）。</li>
 * </ul>
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class TenantSelfQueryTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private JwtTokenProvider jwtTokenProvider;

    @Autowired
    private TenantService tenantService;

    /** 被测租户自身 ID 与登录 token */
    private Long tenantId;
    private String tenantToken;

    @BeforeEach
    void setUp() {
        // 租户表无 tenant_id 列，造数据时放行租户过滤
        TenantContextHolder.setTenantId(1L);
        TenantContextHolder.setIgnore(true);

        String username = "tenant-self-" + System.nanoTime();
        Tenant tenant = new Tenant();
        tenant.setName("自查测试租户");
        tenant.setCode("T-" + System.nanoTime());
        tenant.setUsername(username);
        tenant.setPassword("secret123");
        tenant.setMiniappAppid("wxappid-" + System.nanoTime());
        tenant.setMiniappSecret("miniapp-secret-test");
        tenant.setMerchantNo("MCH-123456");
        tenant.setContactName("联系人");
        tenant.setContactPhone("13900000000");
        tenantService.save(tenant);
        tenantId = tenant.getId();

        TenantContextHolder.clear();

        // 租户 token：role=7，subject=username，userId/tenantId 均为租户自身 ID（对齐 AuthServiceImpl.tenantLogin）
        tenantToken = jwtTokenProvider.generateToken(tenantId, username, tenantId, 7);
    }

    @AfterEach
    void tearDown() {
        TenantContextHolder.clear();
    }

    @Test
    void tenantCanReadOwnProfileWithoutSensitiveFields() throws Exception {
        mockMvc.perform(get("/api/system/tenant/me").header("Authorization", "Bearer " + tenantToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(200))
                .andExpect(jsonPath("$.data.id").value(tenantId))
                .andExpect(jsonPath("$.data.name").value("自查测试租户"))
                .andExpect(jsonPath("$.data.merchantNo").value("MCH-123456"))
                .andExpect(jsonPath("$.data.password").doesNotExist())
                .andExpect(jsonPath("$.data.miniappSecret").doesNotExist());
    }

    @Test
    void tenantForbiddenFromFullTenantList() throws Exception {
        mockMvc.perform(get("/api/system/tenant").header("Authorization", "Bearer " + tenantToken))
                .andExpect(status().isForbidden());
    }
}
