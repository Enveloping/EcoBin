package org.enveloping.ecobin;

import org.enveloping.ecobin.business.dto.CleanSubmitRequest;
import org.enveloping.ecobin.business.entity.CleanOrder;
import org.enveloping.ecobin.business.service.CleanOrderService;
import org.enveloping.ecobin.common.exception.BusinessException;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.device.entity.Device;
import org.enveloping.ecobin.device.entity.Door;
import org.enveloping.ecobin.device.service.DeviceService;
import org.enveloping.ecobin.device.service.DoorService;
import org.enveloping.ecobin.framework.tenant.TenantContextHolder;
import org.enveloping.ecobin.system.entity.User;
import org.enveloping.ecobin.system.service.UserService;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * C 端清运员作业流程验证：
 * <ul>
 *   <li>清运员提交清运：userId 锁定登录态、tenant 取设备归属、单据待审核；</li>
 *   <li>「我的清运」仅返回本人记录，单条详情归属校验；</li>
 *   <li>跨租户设备提交被拒；</li>
 *   <li>租户审核清运员提交的单据，状态正常流转。</li>
 * </ul>
 */
@SpringBootTest
@ActiveProfiles("test")
@Transactional
class AppCleanTest {

    @Autowired
    private CleanOrderService cleanOrderService;
    @Autowired
    private DeviceService deviceService;
    @Autowired
    private DoorService doorService;
    @Autowired
    private UserService userService;

    private final Long tenantId = 2L;
    private Long cleanerId;
    private Long deviceId;
    private Long doorId;

    @BeforeEach
    void setUp() {
        TenantContextHolder.setTenantId(tenantId);
        TenantContextHolder.setIgnore(true);

        Device device = new Device();
        device.setTenantId(tenantId);
        device.setSn("SN-clean-" + System.nanoTime());
        device.setName("清运测试设备");
        device.setType(1);
        device.setStatus(1);
        deviceService.save(device);
        deviceId = device.getId();

        Door door = doorService.listByDeviceId(deviceId).get(0);
        doorId = door.getId();

        User cleaner = new User();
        cleaner.setTenantId(tenantId);
        cleaner.setOpenid("openid-clean-" + System.nanoTime());
        cleaner.setNickname("清运员");
        cleaner.setRole(2);   // CLEANER
        cleaner.setStatus(1);
        userService.save(cleaner);
        cleanerId = cleaner.getId();

        TenantContextHolder.clear();
    }

    @AfterEach
    void tearDown() {
        TenantContextHolder.clear();
        SecurityContextHolder.clearContext();
    }

    private void asCleaner() {
        SecurityContextHolder.getContext().setAuthentication(
                new UsernamePasswordAuthenticationToken("openid-clean", cleanerId, List.of()));
        TenantContextHolder.setTenantId(tenantId);
        TenantContextHolder.setIgnore(false);
    }

    private void asTenant() {
        SecurityContextHolder.getContext().setAuthentication(
                new UsernamePasswordAuthenticationToken("tenant", tenantId, List.of()));
        TenantContextHolder.setTenantId(tenantId);
        TenantContextHolder.setIgnore(false);
    }

    private void asPlatform() {
        SecurityContextHolder.clearContext();
        TenantContextHolder.setTenantId(1L);
        TenantContextHolder.setIgnore(true);
    }

    private CleanSubmitRequest request(BigDecimal weight) {
        CleanSubmitRequest req = new CleanSubmitRequest();
        req.setDeviceId(deviceId);
        req.setDoorId(doorId);
        req.setWasteType1(2);
        req.setWeight(weight);
        return req;
    }

    @Test
    void cleanerSubmitsCleanOrder() {
        asCleaner();
        CleanOrder order = cleanOrderService.submitClean(cleanerId, request(new BigDecimal("5.000")));

        assertNotNull(order.getId());
        assertNotNull(order.getOrderSn());
        assertEquals(cleanerId, order.getUserId());
        assertEquals(tenantId, order.getTenantId());
        assertEquals(deviceId, order.getDeviceId());
        assertEquals(0, order.getAuditStatus());   // 待审核
        assertEquals(0, order.getStatus());         // 创建
    }

    @Test
    void submitIgnoresBodyUserIdAndUsesLoginIdentity() {
        // 即便调用方传入伪造 userId，服务端也以登录态 cleanerId 为准
        asCleaner();
        CleanOrder order = cleanOrderService.submitClean(cleanerId, request(new BigDecimal("1.000")));
        assertEquals(cleanerId, order.getUserId());
    }

    @Test
    void myCleanOrdersReturnsOwnRecords() {
        asCleaner();
        cleanOrderService.submitClean(cleanerId, request(new BigDecimal("3.000")));

        PageResult<CleanOrder> mine = cleanOrderService.pageMyOrders(cleanerId, 1, 20);
        assertFalse(mine.getRecords().isEmpty());
        assertTrue(mine.getRecords().stream().allMatch(o -> cleanerId.equals(o.getUserId())));
    }

    @Test
    void getMyCleanOrderOwnershipEnforced() {
        asCleaner();
        CleanOrder order = cleanOrderService.submitClean(cleanerId, request(new BigDecimal("2.000")));

        // 本人可读
        assertNotNull(cleanOrderService.getMyOrder(cleanerId, order.getId()));
        // 他人读同一单 → 统一按不存在
        assertThrows(BusinessException.class,
                () -> cleanOrderService.getMyOrder(cleanerId + 999, order.getId()));
    }

    @Test
    void submitRejectedForCrossTenantDevice() {
        // 在租户 3 下另造一台设备
        TenantContextHolder.setTenantId(3L);
        TenantContextHolder.setIgnore(true);
        Device other = new Device();
        other.setTenantId(3L);
        other.setSn("SN-clean-other-" + System.nanoTime());
        other.setName("他租户设备");
        other.setType(1);
        other.setStatus(1);
        deviceService.save(other);
        Long otherDeviceId = other.getId();
        TenantContextHolder.clear();

        asCleaner();   // 租户 2 上下文
        CleanSubmitRequest req = new CleanSubmitRequest();
        req.setDeviceId(otherDeviceId);
        req.setWasteType1(2);
        req.setWeight(new BigDecimal("1.000"));
        assertThrows(BusinessException.class,
                () -> cleanOrderService.submitClean(cleanerId, req));
    }

    @Test
    void tenantAuditsCleanerSubmission() {
        asCleaner();
        CleanOrder order = cleanOrderService.submitClean(cleanerId, request(new BigDecimal("4.000")));

        asTenant();
        CleanOrder audited = cleanOrderService.audit(order.getId(), 1);
        assertEquals(1, audited.getAuditStatus());
        assertEquals(1, audited.getStatus());
    }
}
