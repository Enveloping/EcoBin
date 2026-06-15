package org.enveloping.ecobin;

import org.enveloping.ecobin.business.dto.CleanGrossRequest;
import org.enveloping.ecobin.business.dto.CleanTareRequest;
import org.enveloping.ecobin.business.entity.CleanBag;
import org.enveloping.ecobin.business.entity.CleanOrder;
import org.enveloping.ecobin.business.service.CleanBagService;
import org.enveloping.ecobin.business.service.CleanOrderService;
import org.enveloping.ecobin.device.entity.Device;
import org.enveloping.ecobin.device.entity.Door;
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
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 清运流程验证（开门即建单 + 设备只认 cleanOrderId + 去皮链式追踪）：
 * <ul>
 *   <li>清运员 open 建单：userId 取登录态、新空袋编号记在订单 newBagQr；</li>
 *   <li>首次毛重上报（无垃圾袋记录）→ net == 毛重、tare == 0；</li>
 *   <li>换新空袋去皮上报 → 该投口当前垃圾袋去皮被写入（新袋取自订单，设备不传 bagNo）；</li>
 *   <li>第二次清运毛重 → net == 毛重 − 上次去皮，清走的是旧袋；</li>
 *   <li>同一 cleanOrderId 重复毛重上报 → 幂等，不覆盖。</li>
 * </ul>
 */
@SpringBootTest
@ActiveProfiles("test")
@Transactional
class CleanFlowTest {

    @Autowired
    private CleanOrderService cleanOrderService;
    @Autowired
    private CleanBagService cleanBagService;
    @Autowired
    private DeviceService deviceService;
    @Autowired
    private DoorService doorService;

    private final Long tenantId = 2L;
    private final Long cleanerId = 1001L;
    private Long deviceId;
    private Long doorId;
    private Integer doorIndex;
    private String deviceSn;

    @BeforeEach
    void setUp() {
        TenantContextHolder.setTenantId(tenantId);
        TenantContextHolder.setIgnore(true);
        deviceSn = "SN-clean-" + System.nanoTime();
        Device device = new Device();
        device.setTenantId(tenantId);
        device.setSn(deviceSn);
        device.setName("清运流程测试设备");
        device.setType(1);
        device.setStatus(1);
        deviceService.save(device);
        deviceId = device.getId();
        Door door = doorService.listByDeviceId(deviceId).get(0);
        doorId = door.getId();
        doorIndex = door.getDoorIndex();
        TenantContextHolder.clear();
    }

    @AfterEach
    void tearDown() {
        TenantContextHolder.clear();
        SecurityContextHolder.clearContext();
    }

    /** 模拟设备 IoT 调用（无登录态，放行租户过滤，与 JwtFilter 对无 token 请求一致） */
    private void asDevice() {
        SecurityContextHolder.clearContext();
        TenantContextHolder.setTenantId(1L);
        TenantContextHolder.setIgnore(true);
    }

    /** 模拟 C 端清运员登录态 */
    private void asCleaner() {
        SecurityContextHolder.getContext().setAuthentication(
                new UsernamePasswordAuthenticationToken("openid-clean", cleanerId, List.of()));
        TenantContextHolder.setTenantId(tenantId);
        TenantContextHolder.setIgnore(false);
    }

    /** 清运员开清运门：建单（登录态），返回 cleanOrderId */
    private Long openOrder(String newBag) {
        asCleaner();
        return cleanOrderService.openCleanDoor(doorId, newBag).getId();
    }

    private CleanGrossRequest gross(Long orderId, BigDecimal weight) {
        CleanGrossRequest req = new CleanGrossRequest();
        req.setSn(deviceSn);
        req.setCleanOrderId(orderId);
        req.setWeight(weight);
        return req;
    }

    private CleanTareRequest tare(Long orderId, BigDecimal weight) {
        CleanTareRequest req = new CleanTareRequest();
        req.setSn(deviceSn);
        req.setCleanOrderId(orderId);
        req.setWeight(weight);
        return req;
    }

    @Test
    void openCleanDoorCreatesOrder() {
        asCleaner();
        CleanOrder order = cleanOrderService.openCleanDoor(doorId, "BAG-NEW");

        assertNotNull(order.getId());
        assertEquals(cleanerId, order.getUserId());        // userId 取自登录态，非设备
        assertEquals("BAG-NEW", order.getNewBagQr());      // 新空袋记在订单
        assertEquals(0, order.getStatus());
        assertEquals(0, order.getAuditStatus());
        assertNull(order.getGrossWeight());                // 毛重待设备上报
        assertEquals(tenantId, order.getTenantId());
        assertEquals(doorId, order.getDoorId());
        // 开门即按 cleanOrderId 预存 4 张照片 URL（设备直传到对应 key，无需回传）
        String token = String.valueOf(order.getId());
        assertTrue(order.getPhotoOpenOutside().endsWith("/" + token + "/open_outside.jpg"));
        assertTrue(order.getPhotoOpenInside().endsWith("/" + token + "/open_inside.jpg"));
        assertTrue(order.getPhotoCloseOutside().endsWith("/" + token + "/close_outside.jpg"));
        assertTrue(order.getPhotoCloseInside().endsWith("/" + token + "/close_inside.jpg"));
    }

    @Test
    void firstGrossHasZeroTare() {
        Long orderId = openOrder("BAG-001");
        asDevice();
        CleanOrder order = cleanOrderService.reportGross(gross(orderId, new BigDecimal("10.000")));

        assertEquals(0, new BigDecimal("10.000").compareTo(order.getGrossWeight()));
        assertEquals(0, BigDecimal.ZERO.compareTo(order.getTareWeight()));   // 首次无去皮
        assertEquals(0, new BigDecimal("10.000").compareTo(order.getNetWeight()));
        assertNull(order.getBagQr());                                        // 尚无当前(旧)垃圾袋
        assertEquals(cleanerId, order.getUserId());                          // 建单时的登录清运员
    }

    @Test
    void tareThenSecondGrossDeductsTare() {
        // 第一次清运：开门(新袋 BAG-001) → 毛重 10(去皮 0) → 去皮上报 1.0
        Long o1 = openOrder("BAG-001");
        asDevice();
        cleanOrderService.reportGross(gross(o1, new BigDecimal("10.000")));
        cleanOrderService.reportTare(tare(o1, new BigDecimal("1.000")));
        CleanBag bag = cleanBagService.getCurrent(deviceId, doorIndex);
        assertNotNull(bag);
        assertEquals("BAG-001", bag.getBagQr());
        assertEquals(0, new BigDecimal("1.000").compareTo(bag.getTareWeight()));

        // 第二次清运：开门(新袋 BAG-002) → 毛重 12 → 实际清运量 = 12 - 1 = 11，清走旧袋 BAG-001
        Long o2 = openOrder("BAG-002");
        asDevice();
        CleanOrder order = cleanOrderService.reportGross(gross(o2, new BigDecimal("12.000")));
        assertEquals(0, new BigDecimal("12.000").compareTo(order.getGrossWeight()));
        assertEquals(0, new BigDecimal("1.000").compareTo(order.getTareWeight()));
        assertEquals(0, new BigDecimal("11.000").compareTo(order.getNetWeight()));
        assertEquals("BAG-001", order.getBagQr());                           // 清走的是旧袋
    }

    @Test
    void replaceBagUpdatesTare() {
        Long o1 = openOrder("BAG-001");
        asDevice();
        cleanOrderService.reportTare(tare(o1, new BigDecimal("1.000")));
        Long o2 = openOrder("BAG-002");
        asDevice();
        cleanOrderService.reportTare(tare(o2, new BigDecimal("2.000")));

        CleanBag bag = cleanBagService.getCurrent(deviceId, doorIndex);
        assertEquals("BAG-002", bag.getBagQr());
        assertEquals(0, new BigDecimal("2.000").compareTo(bag.getTareWeight()));
        // 同投口仅一条当前袋记录
        assertEquals(1, cleanBagService.lambdaQuery()
                .eq(CleanBag::getDeviceId, deviceId)
                .eq(CleanBag::getDoorIndex, doorIndex)
                .count());
    }

    @Test
    void duplicateGrossIsIdempotent() {
        Long orderId = openOrder("BAG-001");
        asDevice();
        CleanOrder first = cleanOrderService.reportGross(gross(orderId, new BigDecimal("5.000")));
        CleanOrder again = cleanOrderService.reportGross(gross(orderId, new BigDecimal("8.000")));

        assertEquals(first.getId(), again.getId());
        // 幂等：第二次不覆盖，仍是首次毛重 5.000
        assertEquals(0, new BigDecimal("5.000").compareTo(again.getGrossWeight()));
    }
}
