package org.enveloping.ecobin;

import org.enveloping.ecobin.business.dto.DeliveryReportRequest;
import org.enveloping.ecobin.business.dto.OpenDoorResult;
import org.enveloping.ecobin.business.entity.DeliveryOrder;
import org.enveloping.ecobin.business.service.DeliveryOrderService;
import org.enveloping.ecobin.common.exception.BusinessException;
import org.enveloping.ecobin.device.entity.Device;
import org.enveloping.ecobin.device.service.DeviceService;
import org.enveloping.ecobin.device.service.DoorService;
import org.enveloping.ecobin.framework.tenant.TenantContextHolder;
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
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 投递两阶段流程验证（开投口建记录 → 设备 IoT 上报回填）。
 * <ul>
 *   <li>阶段1：C 端登录用户开投口，创建「进行中」记录并返回投递标识符；</li>
 *   <li>阶段2：设备按 SN + 标识符上报，回填重量并置为「已完成」；</li>
 *   <li>重复上报被拒绝。</li>
 * </ul>
 */
@SpringBootTest
@ActiveProfiles("test")
@Transactional
class DeliveryTwoPhaseTest {

    @Autowired
    private DeliveryOrderService deliveryOrderService;
    @Autowired
    private DeviceService deviceService;
    @Autowired
    private DoorService doorService;

    private final Long tenantId = 2L;
    private final Long userId = 1001L;
    private Long doorId;
    private String deviceSn;

    @BeforeEach
    void setUp() {
        // 平台域放行租户过滤，显式 tenant=2 造设备（自动生成 doorIndex=1 默认投口）
        TenantContextHolder.setTenantId(tenantId);
        TenantContextHolder.setIgnore(true);
        deviceSn = "SN-2phase-" + System.nanoTime();
        Device device = new Device();
        device.setTenantId(tenantId);
        device.setSn(deviceSn);
        device.setName("两阶段测试设备");
        device.setType(1);
        device.setStatus(1);
        deviceService.save(device);
        doorId = doorService.listByDeviceId(device.getId()).get(0).getId();
        TenantContextHolder.clear();
    }

    @AfterEach
    void tearDown() {
        TenantContextHolder.clear();
        SecurityContextHolder.clearContext();
    }

    /** 模拟 C 端用户登录态（userId 放入 credentials，tenant 隔离生效） */
    private void asLoggedInUser() {
        SecurityContextHolder.getContext().setAuthentication(
                new UsernamePasswordAuthenticationToken("openid-2phase", userId, List.of()));
        TenantContextHolder.setTenantId(tenantId);
        TenantContextHolder.setIgnore(false);
    }

    /** 模拟设备 IoT 调用（无登录态，与 JwtFilter 对无 token 请求一致：放行租户过滤） */
    private void asDevice() {
        SecurityContextHolder.clearContext();
        TenantContextHolder.setTenantId(1L);
        TenantContextHolder.setIgnore(true);
    }

    @Test
    void openThenComplete() {
        asLoggedInUser();
        OpenDoorResult result = deliveryOrderService.openDoor(doorId);
        assertNotNull(result.orderId());
        assertNotNull(result.deliveryToken());

        DeliveryOrder pending = deliveryOrderService.getById(result.orderId());
        assertEquals(0, pending.getDeliveryStatus());   // 进行中
        assertEquals(userId, pending.getUserId());
        assertNull(pending.getWeight());
        // 开门即按 deliveryToken 预存 4 张照片 URL（设备直传到对应 key，无需回传）
        String token = result.deliveryToken();
        assertTrue(pending.getPhotoOpenOutside().endsWith("/" + token + "/open_outside.jpg"));
        assertTrue(pending.getPhotoOpenInside().endsWith("/" + token + "/open_inside.jpg"));
        assertTrue(pending.getPhotoCloseOutside().endsWith("/" + token + "/close_outside.jpg"));
        assertTrue(pending.getPhotoCloseInside().endsWith("/" + token + "/close_inside.jpg"));

        asDevice();
        DeliveryReportRequest report = new DeliveryReportRequest();
        report.setSn(deviceSn);
        report.setDeliveryToken(result.deliveryToken());
        report.setWeight(new BigDecimal("1.234"));
        deliveryOrderService.completeDelivery(report);

        DeliveryOrder done = deliveryOrderService.getById(result.orderId());
        assertEquals(1, done.getDeliveryStatus());       // 已完成
        assertEquals(0, new BigDecimal("1.234").compareTo(done.getWeight()));
    }

    @Test
    void duplicateReportRejected() {
        asLoggedInUser();
        OpenDoorResult result = deliveryOrderService.openDoor(doorId);

        asDevice();
        DeliveryReportRequest report = new DeliveryReportRequest();
        report.setSn(deviceSn);
        report.setDeliveryToken(result.deliveryToken());
        report.setWeight(new BigDecimal("1.0"));
        deliveryOrderService.completeDelivery(report);

        assertThrows(BusinessException.class, () -> deliveryOrderService.completeDelivery(report));
    }
}
