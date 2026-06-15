package org.enveloping.ecobin;

import org.enveloping.ecobin.business.dto.DeliveryReportRequest;
import org.enveloping.ecobin.business.entity.DeliveryOrder;
import org.enveloping.ecobin.business.service.DeliveryOrderService;
import org.enveloping.ecobin.device.entity.Device;
import org.enveloping.ecobin.device.service.DeviceService;
import org.enveloping.ecobin.device.service.DeviceSessionService;
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

/**
 * 投递流程验证（开启设备建会话 → 设备上传后建单 → 会话关联用户）。
 * <ul>
 *   <li>开启设备：写「当前活跃用户」会话，不建单；</li>
 *   <li>设备上传：上传后建单（deliveryStatus=1）、归属取活跃会话用户、存设备回传的 4 张照片 URL；</li>
 *   <li>无活跃会话上传：建无主单（userId=null）、不返现；</li>
 *   <li>同 msgId（OneNet 消息 id）重复上传：幂等，不重复建单。</li>
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
    @Autowired
    private DeviceSessionService deviceSessionService;

    private final Long tenantId = 2L;
    private final Long userId = 1001L;
    private Long deviceId;
    private Long doorId;
    private Integer doorIndex;
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
        device.setName("投递流程测试设备");
        device.setType(1);
        device.setStatus(1);
        deviceService.save(device);
        deviceId = device.getId();
        var door = doorService.listByDeviceId(deviceId).get(0);
        doorId = door.getId();
        doorIndex = door.getDoorIndex();
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

    /** 设备直传后回传的照片 URL 前缀（位置由设备决定，后端原样存）。 */
    private String photoBase() {
        return "https://bucket.cos.ap-shanghai.myqcloud.com/" + deviceSn + "/" + doorIndex + "/dev/";
    }

    private DeliveryReportRequest report(String weight) {
        DeliveryReportRequest req = new DeliveryReportRequest();
        req.setSn(deviceSn);
        req.setDoorIndex(doorIndex);
        req.setWeight(new BigDecimal(weight));
        // 模拟设备直传 COS 后随本次称重上报回传的 4 个照片 URL
        String base = photoBase();
        req.setPhotoOpenOutside(base + "open_outside.jpg");
        req.setPhotoOpenInside(base + "open_inside.jpg");
        req.setPhotoCloseOutside(base + "close_outside.jpg");
        req.setPhotoCloseInside(base + "close_inside.jpg");
        return req;
    }

    /** 查该设备最新一条投递单（已去 deliveryToken，无幂等键，按 id 倒序取最新）。 */
    private DeliveryOrder findLatest() {
        return deliveryOrderService.lambdaQuery()
                .eq(DeliveryOrder::getDeviceId, deviceId)
                .orderByDesc(DeliveryOrder::getId)
                .last("limit 1")
                .one();
    }

    @Test
    void openCreatesSessionNoOrder() {
        asLoggedInUser();
        deliveryOrderService.openDoor(doorId);

        // 开启设备只建会话，不建单
        assertNotNull(deviceSessionService.findActive(deviceId));
        assertEquals(userId, deviceSessionService.findActive(deviceId).getUserId());
        asDevice();
        assertNull(findLatest());   // 仅开启设备，未上传 → 无单
    }

    @Test
    void uploadCreatesOrderAttributedToActiveUser() {
        asLoggedInUser();
        deliveryOrderService.openDoor(doorId);

        asDevice();
        deliveryOrderService.completeDelivery(report("1.234"));

        DeliveryOrder done = findLatest();
        assertNotNull(done);
        assertEquals(1, done.getDeliveryStatus());                 // 上传即完成
        assertEquals(userId, done.getUserId());                    // 归活跃会话用户
        assertEquals(tenantId, done.getTenantId());
        assertEquals(0, new BigDecimal("1.234").compareTo(done.getWeight()));
        // 照片 URL：设备随上报回传，后端原样存
        assertEquals(photoBase() + "open_outside.jpg", done.getPhotoOpenOutside());
        assertEquals(photoBase() + "open_inside.jpg", done.getPhotoOpenInside());
        assertEquals(photoBase() + "close_outside.jpg", done.getPhotoCloseOutside());
        assertEquals(photoBase() + "close_inside.jpg", done.getPhotoCloseInside());
    }

    @Test
    void uploadWithoutSessionCreatesUnownedOrder() {
        // 未开启设备（无活跃会话）直接上传 → 无主单
        asDevice();
        deliveryOrderService.completeDelivery(report("2.000"));

        DeliveryOrder order = findLatest();
        assertNotNull(order);
        assertNull(order.getUserId());               // 无主单
        assertEquals(tenantId, order.getTenantId()); // 归设备租户
        assertEquals(1, order.getDeliveryStatus());
    }

    @Test
    void duplicateUploadSameMsgIdIsIdempotent() {
        asLoggedInUser();
        deliveryOrderService.openDoor(doorId);

        asDevice();
        String msgId = "mq-" + System.nanoTime();
        DeliveryReportRequest first = report("1.0");
        first.setMsgId(msgId);
        DeliveryReportRequest dup = report("9.9");
        dup.setMsgId(msgId);                                  // 同 OneNet 消息 id

        deliveryOrderService.completeDelivery(first);
        deliveryOrderService.completeDelivery(dup);           // MQ 重投，按 device+msgId 幂等忽略

        long count = deliveryOrderService.lambdaQuery()
                .eq(DeliveryOrder::getDeviceId, deviceId)
                .count();
        assertEquals(1, count);
        assertEquals(0, new BigDecimal("1.0").compareTo(findLatest().getWeight()));   // 仍是首次重量
    }
}
