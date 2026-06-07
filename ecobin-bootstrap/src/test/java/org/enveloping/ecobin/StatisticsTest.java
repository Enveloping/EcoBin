package org.enveloping.ecobin;

import org.enveloping.ecobin.business.dto.DeliveryReportRequest;
import org.enveloping.ecobin.business.dto.OpenDoorResult;
import org.enveloping.ecobin.business.entity.DeliveryOrder;
import org.enveloping.ecobin.business.service.DeliveryOrderService;
import org.enveloping.ecobin.business.service.StatisticsService;
import org.enveloping.ecobin.device.entity.Device;
import org.enveloping.ecobin.device.entity.Door;
import org.enveloping.ecobin.device.service.DeviceService;
import org.enveloping.ecobin.device.service.DoorService;
import org.enveloping.ecobin.framework.tenant.TenantContextHolder;
import org.enveloping.ecobin.system.entity.User;
import org.enveloping.ecobin.business.entity.WithdrawOrder;
import org.enveloping.ecobin.system.service.UserService;
import org.enveloping.ecobin.business.service.WalletService;
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
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * 统计接口验证（9 端点）。
 */
@SpringBootTest
@ActiveProfiles("test")
@Transactional
class StatisticsTest {

    @Autowired
    private StatisticsService statisticsService;
    @Autowired
    private DeliveryOrderService deliveryOrderService;
    @Autowired
    private DeviceService deviceService;
    @Autowired
    private DoorService doorService;
    @Autowired
    private WalletService walletService;
    @Autowired
    private UserService userService;

    private final Long tenantId = 2L;
    private Long userId;
    private Long doorId;
    private String deviceSn;

    @BeforeEach
    void setUp() {
        TenantContextHolder.setTenantId(tenantId);
        TenantContextHolder.setIgnore(true);

        // 创建设备 + 投口（配单价）
        deviceSn = "SN-stat-" + System.nanoTime();
        Device device = new Device();
        device.setTenantId(tenantId);
        device.setSn(deviceSn);
        device.setName("统计测试设备");
        device.setType(1);
        device.setStatus(1);
        device.setLat(new BigDecimal("36.975"));
        device.setLng(new BigDecimal("117.161"));
        deviceService.save(device);

        Door door = doorService.listByDeviceId(device.getId()).get(0);
        doorId = door.getId();
        door.setPrice(new BigDecimal("2.00"));
        doorService.updateById(door);

        // 创建用户
        User user = new User();
        user.setTenantId(tenantId);
        user.setOpenid("openid-stat-" + System.nanoTime());
        user.setNickname("统计测试用户");
        user.setRole(1);     // USER
        user.setStatus(1);
        userService.save(user);
        userId = user.getId();

        TenantContextHolder.clear();
    }

    @AfterEach
    void tearDown() {
        TenantContextHolder.clear();
        SecurityContextHolder.clearContext();
    }

    private void asUser() {
        SecurityContextHolder.getContext().setAuthentication(
                new UsernamePasswordAuthenticationToken("openid-stat", userId, List.of()));
        TenantContextHolder.setTenantId(tenantId);
        TenantContextHolder.setIgnore(false);
    }

    private void asDevice() {
        SecurityContextHolder.clearContext();
        TenantContextHolder.setTenantId(1L);
        TenantContextHolder.setIgnore(true);
    }

    private void asPlatform() {
        SecurityContextHolder.clearContext();
        TenantContextHolder.setTenantId(1L);
        TenantContextHolder.setIgnore(true);
    }

    private void asTenant() {
        SecurityContextHolder.getContext().setAuthentication(
                new UsernamePasswordAuthenticationToken("tenant", tenantId, List.of()));
        TenantContextHolder.setTenantId(tenantId);
        TenantContextHolder.setIgnore(false);
    }

    /** 走完投递两阶段流程以产生投递数据 */
    private void doDelivery(double weight) {
        asUser();
        OpenDoorResult result = deliveryOrderService.openDoor(doorId);
        asDevice();
        DeliveryReportRequest report = new DeliveryReportRequest();
        report.setSn(deviceSn);
        report.setDeliveryToken(result.deliveryToken());
        report.setWeight(new BigDecimal(String.valueOf(weight)));
        deliveryOrderService.completeDelivery(report);
    }

    @Test
    void dashboardReturnsTodayStats() {
        doDelivery(1.5);

        asPlatform();
        Map<String, Object> dashboard = statisticsService.dashboard();

        assertTrue((Long) dashboard.get("deliveryCount") > 0);
        assertTrue((Double) dashboard.get("totalWeight") > 0);
        assertTrue((Long) dashboard.get("todayMemberCount") > 0);
    }

    @Test
    void deviceStatsReturnsTotalCount() {
        asPlatform();
        Map<String, Object> stats = statisticsService.deviceStats();
        assertTrue((Long) stats.get("totalCount") > 0);
        assertEquals(0, stats.get("onlinkCount"));
        assertEquals(0, stats.get("spillCount"));
        assertEquals(0, stats.get("smokeCount"));
    }

    @Test
    void memberStatsReturnsCounts() {
        asPlatform();
        Map<String, Object> stats = statisticsService.memberStats();
        assertTrue((Long) stats.get("memberCount") > 0);
        assertNotNull(stats.get("todayMemberCount"));
        assertNotNull(stats.get("memberDisableCount"));
    }

    @Test
    void deliveryStatsReturnsMonthAggregates() {
        doDelivery(1.5);

        asPlatform();
        Map<String, Object> stats = statisticsService.deliveryStats();
        assertTrue((Long) stats.get("deliveryCount") > 0);
        assertTrue((Double) stats.get("deliveryWeight") > 0);
        // price(2.00) × weight(1.5) = 3.00
        double money = (Double) stats.get("deliveryMoney");
        assertTrue(money > 0, "投递金额应 >0");
    }

    @Test
    void cleanStatsReturnsMonthAggregates() {
        asPlatform();
        Map<String, Object> stats = statisticsService.cleanStats();
        assertNotNull(stats.get("cleanCount"));
        assertNotNull(stats.get("totalWeights"));
    }

    @Test
    void payoutStatsReturnsAggregates() {
        // 先充值，再发起提现
        asPlatform();
        walletService.income(userId, tenantId, new BigDecimal("10.00"), null);
        asUser();
        WithdrawOrder order = walletService.applyWithdraw(userId, new BigDecimal("4.00"));
        asTenant();
        walletService.auditWithdraw(order.getId(), true, "通过");

        asPlatform();
        Map<String, Object> stats = statisticsService.payoutStats();
        assertTrue((Long) stats.get("payOutCount") > 0);
        assertTrue((Double) stats.get("pushSuccessMoney") > 0, "通过金额应 >0");
    }

    @Test
    void memberMoneyStatsReturnsBalanceSums() {
        // 充值给用户
        asPlatform();
        walletService.income(userId, tenantId, new BigDecimal("10.00"), null);

        Map<String, Object> stats = statisticsService.memberMoneyStats();
        assertTrue((Long) stats.get("memberCount") > 0);
        double memberMoney = (Double) stats.get("memberMoney");
        assertTrue(memberMoney > 0, "会员余额总和应 >0");
    }

    @Test
    void devicesMapReturnsCoordinateData() {
        asPlatform();
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> list = statisticsService.devicesMap();
        assertFalse(list.isEmpty(), "设备地图应返回数据");
        Map<String, Object> first = list.get(0);
        assertNotNull(first.get("id"));
        assertNotNull(first.get("sn"));
        assertNotNull(first.get("lat"));
        assertNotNull(first.get("lng"));
    }

    @Test
    void deviceRankingReturnsOrderedByWeight() {
        doDelivery(3.0);

        asPlatform();
        List<Map<String, Object>> ranking = statisticsService.deviceRanking(5);
        assertFalse(ranking.isEmpty());
        double prev = Double.MAX_VALUE;
        for (Map<String, Object> r : ranking) {
            // H2 返回大写 TOTAL_WEIGHT，MySQL 返回小写 total_weight
            Object weightObj = r.containsKey("total_weight")
                    ? r.get("total_weight")
                    : r.get("TOTAL_WEIGHT");
            assertNotNull(weightObj, "应包含权重列");
            double w = ((Number) weightObj).doubleValue();
            assertTrue(w <= prev, "排行应按重量降序");
            prev = w;
        }
    }
}
