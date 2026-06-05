package org.enveloping.ecobin;

import org.enveloping.ecobin.business.entity.DeliveryOrder;
import org.enveloping.ecobin.business.service.DeliveryOrderService;
import org.enveloping.ecobin.framework.security.JwtTokenProvider;
import org.enveloping.ecobin.framework.tenant.TenantContextHolder;
import org.enveloping.ecobin.system.entity.User;
import org.enveloping.ecobin.system.service.UserService;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
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
    private UserService userService;

    @Autowired
    private DeliveryOrderService deliveryOrderService;

    /** 租户2 下的终端用户（role=1）及其 token */
    private String userToken;

    @BeforeEach
    void setUp() {
        // 在租户2 下造一个微信终端用户（无 username/password），显式 tenant_id=2
        TenantContextHolder.setTenantId(2L);
        TenantContextHolder.setIgnore(true);
        User user = new User();
        user.setTenantId(2L);
        user.setOpenid("openid-app-sec-" + System.nanoTime());
        user.setNickname("测试用户");
        user.setAvatar("http://example.com/a.png");
        user.setRealName("张三");
        user.setPhone("13800000000");
        user.setRole(1);
        user.setStatus(1);
        userService.save(user);
        Long userId = user.getId();

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
        userToken = jwtTokenProvider.generateToken(userId, user.getOpenid(), 2L, 1);
    }

    @AfterEach
    void tearDown() {
        TenantContextHolder.clear();
    }

    @Test
    void terminalUserCanReadOwnProfileWithoutSensitiveFields() throws Exception {
        mockMvc.perform(get("/api/app/profile").header("Authorization", "Bearer " + userToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(200))
                .andExpect(jsonPath("$.data.nickname").value("测试用户"))
                .andExpect(jsonPath("$.data.openid").doesNotExist())
                .andExpect(jsonPath("$.data.password").doesNotExist());
    }

    @Test
    void terminalUserCanListOwnDeliveryRecords() throws Exception {
        mockMvc.perform(get("/api/app/delivery/my").header("Authorization", "Bearer " + userToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(200))
                .andExpect(jsonPath("$.data.total").value(1))
                .andExpect(jsonPath("$.data.records[0].userId").exists());
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
}
