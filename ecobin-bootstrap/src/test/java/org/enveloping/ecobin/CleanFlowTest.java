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

/**
 * 清运流程验证（去皮链式追踪）：
 * <ul>
 *   <li>首次毛重上报（无垃圾袋记录）→ net == 毛重、tare == 0；</li>
 *   <li>换新空袋去皮上报 → 该投口当前垃圾袋去皮被写入；</li>
 *   <li>第二次毛重上报 → net == 毛重 − 上次去皮；</li>
 *   <li>相同 reportSn 重复上报 → 幂等，不重复建单。</li>
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

    private CleanGrossRequest gross(BigDecimal weight, String reportSn) {
        CleanGrossRequest req = new CleanGrossRequest();
        req.setSn(deviceSn);
        req.setDoorIndex(doorIndex);
        req.setUserId(cleanerId);
        req.setWeight(weight);
        req.setReportSn(reportSn);
        return req;
    }

    private CleanTareRequest tare(String bagNo, BigDecimal weight) {
        CleanTareRequest req = new CleanTareRequest();
        req.setSn(deviceSn);
        req.setDoorIndex(doorIndex);
        req.setUserId(cleanerId);
        req.setBagNo(bagNo);
        req.setWeight(weight);
        return req;
    }

    @Test
    void firstGrossHasZeroTare() {
        asDevice();
        CleanOrder order = cleanOrderService.reportGross(gross(new BigDecimal("10.000"), null));

        assertNotNull(order.getId());
        assertEquals(0, new BigDecimal("10.000").compareTo(order.getGrossWeight()));
        assertEquals(0, BigDecimal.ZERO.compareTo(order.getTareWeight()));   // 首次无去皮
        assertEquals(0, new BigDecimal("10.000").compareTo(order.getNetWeight()));
        assertNull(order.getBagQr());                                        // 尚无绑定垃圾袋
        assertEquals(tenantId, order.getTenantId());
        assertEquals(doorId, order.getDoorId());
    }

    @Test
    void tareThenSecondGrossDeductsTare() {
        asDevice();
        // 换上新空袋，上报去皮
        cleanOrderService.reportTare(tare("BAG-001", new BigDecimal("1.000")));
        CleanBag bag = cleanBagService.getCurrent(deviceId, doorIndex);
        assertNotNull(bag);
        assertEquals("BAG-001", bag.getBagQr());
        assertEquals(0, new BigDecimal("1.000").compareTo(bag.getTareWeight()));

        // 下一次清运毛重 12.000 → 实际清运量 = 12 - 1 = 11
        CleanOrder order = cleanOrderService.reportGross(gross(new BigDecimal("12.000"), null));
        assertEquals(0, new BigDecimal("12.000").compareTo(order.getGrossWeight()));
        assertEquals(0, new BigDecimal("1.000").compareTo(order.getTareWeight()));
        assertEquals(0, new BigDecimal("11.000").compareTo(order.getNetWeight()));
        assertEquals("BAG-001", order.getBagQr());                           // 清走的是当前绑定袋
    }

    @Test
    void replaceBagUpdatesTare() {
        asDevice();
        cleanOrderService.reportTare(tare("BAG-001", new BigDecimal("1.000")));
        cleanOrderService.reportTare(tare("BAG-002", new BigDecimal("2.000")));

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
        asDevice();
        CleanOrder first = cleanOrderService.reportGross(gross(new BigDecimal("5.000"), "RS-1"));
        CleanOrder again = cleanOrderService.reportGross(gross(new BigDecimal("5.000"), "RS-1"));

        assertEquals(first.getId(), again.getId());
        assertEquals(1, cleanOrderService.lambdaQuery()
                .eq(CleanOrder::getOrderSn, "RS-1")
                .count());
    }

    @Test
    void openCleanDoorSucceeds() {
        asCleaner();
        // 下发开清运门（OneNet 占位），不应抛异常
        cleanOrderService.openCleanDoor(doorId, "BAG-NEW");
    }
}
