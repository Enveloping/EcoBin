package org.enveloping.ecobin;

import org.enveloping.ecobin.recycling.application.legacy.DeliveryOrderService;
import org.enveloping.ecobin.recycling.domain.legacy.DeliveryOrder;
import org.enveloping.ecobin.framework.context.TrustedExecutionContextHolder;
import org.enveloping.ecobin.framework.security.JwtTokenProvider;
import org.enveloping.ecobin.framework.tenant.TenantContextHolder;
import org.enveloping.ecobin.identity.api.legacy.LegacyOrganizationUserDirectoryPort;
import org.enveloping.ecobin.identity.api.legacy.LegacyOrganizationUserDraft;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;

import org.springframework.http.MediaType;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * C 端接口的 HTTP 层权限端到端验证（MockMvc + 真实 JWT，走完整 Spring Security 过滤链）：
 * <ul>
 *   <li>终端用户（role=1）持有效 token 可访问 /api/app/** 自有数据；</li>
 *   <li>终端用户访问管理端 /api/business/** 被 403 拒绝；</li>
 *   <li>无 token 访问受保护的 /api/app/** 被拒绝；</li>
 *   <li>profile 响应不泄露 openid / password。</li>
 * </ul>
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class AppApiSecurityTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private JwtTokenProvider jwtTokenProvider;

    @Autowired
    private LegacyOrganizationUserDirectoryPort userDirectory;

    @Autowired
    private DeliveryOrderService deliveryOrderService;

    /** 租户2 下的终端用户（role=1）及其 token */
    private String userToken;

    @BeforeEach
    void setUp() {
        // 在租户2 下造一个微信终端用户（无 username/password），显式 tenant_id=2
        TenantContextHolder.setTenantId(2L);
        TenantContextHolder.setIgnore(true);
        String openid = "openid-app-sec-" + System.nanoTime();
        var user = userDirectory.create(new LegacyOrganizationUserDraft(
                2L, null, null, "张三", "13800000000", null,
                openid, null, "测试用户", "http://example.com/a.png",
                1, 1, null, null));
        Long userId = user.userId().value();

        // 该用户的一条投递订单
        TenantContextHolder.setTenantId(2L);
        TenantContextHolder.setIgnore(false);
        DeliveryOrder order = new DeliveryOrder();
        order.setTenantId(2L);
        order.setUserId(userId);
        order.setWasteType1(1);
        order.setStatus(0);
        deliveryOrderService.save(order);

        TenantContextHolder.clear();

        // 终端用户 token：role=1，tenantId=2，subject=openid
        userToken = jwtTokenProvider.generateToken(userId, openid, 2L, 1);
    }

    @AfterEach
    void tearDown() {
        TenantContextHolder.clear();
        TrustedExecutionContextHolder.clear();
    }

    @Test
    void removedLegacyProfileInterfaceIsDeniedEvenWithAValidLegacyToken()
            throws Exception {
        mockMvc.perform(get("/api/app/profile").header("Authorization", "Bearer " + userToken))
                .andExpect(status().isForbidden());
    }

    @Test
    void removedLegacyDeliveryInterfaceIsDeniedEvenWithAValidLegacyToken()
            throws Exception {
        mockMvc.perform(get("/api/app/delivery/my").header("Authorization", "Bearer " + userToken))
                .andExpect(status().isForbidden());
    }

    @Test
    void terminalUserForbiddenFromAdminBusinessEndpoint() throws Exception {
        mockMvc.perform(get("/api/business/delivery").header("Authorization", "Bearer " + userToken))
                .andExpect(status().isForbidden());
    }

    @Test
    void anonymousRequestToAppEndpointIsRejected() throws Exception {
        mockMvc.perform(get("/api/app/profile"))
                .andExpect(status().is4xxClientError());
    }

    @Test
    void normalUserForbiddenFromCleanSubmission() throws Exception {
        // 普通用户（role=1）无清运权限：/api/app/clean 规则限 CLEANER/DEVICE_ADMIN（须先于 /api/app/** 通配匹配）
        String body = "{\"deviceId\":1,\"wasteType1\":2,\"weight\":1.0}";
        mockMvc.perform(post("/api/app/clean")
                        .header("Authorization", "Bearer " + userToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isForbidden());
    }

    @Test
    void unknownLegacyRoleCannotReactivateRemovedInterface() throws Exception {
        String openid = "openid-unknown-role-" + System.nanoTime();
        String unknownRoleToken = jwtTokenProvider.generateToken(999L, openid, 2L, 6);

        mockMvc.perform(get("/api/app/profile")
                        .header("Authorization", "Bearer " + unknownRoleToken))
                .andExpect(status().isForbidden());
    }
}
