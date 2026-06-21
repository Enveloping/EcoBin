package org.enveloping.ecobin;

import org.enveloping.ecobin.business.dto.DeliveryReportRequest;
import org.enveloping.ecobin.business.service.DeliveryOrderService;
import org.enveloping.ecobin.common.exception.BusinessException;
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

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

/**
 * 钱包入账 + 提现流程验证。
 * <ul>
 *   <li>投递完成按「投口单价 × 重量」入账到 sys_user.balance；</li>
 *   <li>发起提现：可用余额转入待审核余额；</li>
 *   <li>审核通过：扣减待审核（资金转出）；审核驳回：待审核退回可用；</li>
 *   <li>余额不足发起提现被拒。</li>
 * </ul>
 */
@SpringBootTest
@ActiveProfiles("test")
@Transactional
class WalletWithdrawTest {

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
    private Integer doorIndex;
    private String deviceSn;

    @BeforeEach
    void setUp() {
        TenantContextHolder.setTenantId(tenantId);
        TenantContextHolder.setIgnore(true);

        deviceSn = "SN-wallet-" + System.nanoTime();
        Device device = new Device();
        device.setTenantId(tenantId);
        device.setSn(deviceSn);
        device.setName("钱包测试设备");
        device.setType(1);
        device.setStatus(1);
        deviceService.save(device);

        // 设备注册自动生成默认投口；配置单价 2.00 元/kg
        Door door = doorService.listByDeviceId(device.getId()).get(0);
        doorId = door.getId();
        doorIndex = door.getDoorIndex();
        door.setPrice(new BigDecimal("2.00"));
        doorService.updateById(door);

        User user = new User();
        user.setTenantId(tenantId);
        user.setOpenid("openid-wallet-" + System.nanoTime());
        user.setNickname("钱包测试用户");
        user.setRole(1);
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
                new UsernamePasswordAuthenticationToken("openid-wallet", userId, List.of()));
        TenantContextHolder.setTenantId(tenantId);
        TenantContextHolder.setIgnore(false);
    }

    private void asTenant() {
        // 租户登录态：credentials = 租户主体ID（审核人）
        SecurityContextHolder.getContext().setAuthentication(
                new UsernamePasswordAuthenticationToken("tenant", tenantId, List.of()));
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

    /** 充值若干余额（走入账，平台上下文放行） */
    private void seedBalance(String amount) {
        asPlatform();
        walletService.income(userId, tenantId, new BigDecimal(amount), null);
    }

    private BigDecimal balanceOf() {
        asPlatform();
        return userService.getById(userId).getBalance();
    }

    private BigDecimal pendingOf() {
        asPlatform();
        return userService.getById(userId).getPendingBalance();
    }

    /** 走完投递流程（开启设备 → 设备上传后建单），返回该设备最新一单 id */
    private Long doDeliveryAndGetOrderId() {
        asUser();
        deliveryOrderService.openDoor(doorId);

        asDevice();
        DeliveryReportRequest report = new DeliveryReportRequest();
        report.setSn(deviceSn);
        report.setDoorIndex(doorIndex);
        report.setWeight(new BigDecimal("1.500"));
        deliveryOrderService.completeDelivery(report);

        return deliveryOrderService.lambdaQuery()
                .orderByDesc(org.enveloping.ecobin.business.entity.DeliveryOrder::getId)
                .last("limit 1")
                .one().getId();
    }

    @Test
    void deliveryCreatesPendingAuditNoCredit() {
        Long orderId = doDeliveryAndGetOrderId();

        // 建单进入「待审核」，钱未入账
        asPlatform();
        var order = deliveryOrderService.getById(orderId);
        assertEquals(0, order.getAuditStatus());
        assertEquals(0, BigDecimal.ZERO.compareTo(balanceOf()));
    }

    @Test
    void auditApproveCreditsBalance() {
        Long orderId = doDeliveryAndGetOrderId();

        asTenant();
        deliveryOrderService.audit(orderId, 1, "通过");

        // 单价 2.00 × 重量 1.5 = 3.00，审核通过后才入账
        assertEquals(0, new BigDecimal("3.00").compareTo(balanceOf()));
    }

    @Test
    void auditRejectDoesNotCredit() {
        Long orderId = doDeliveryAndGetOrderId();

        asTenant();
        deliveryOrderService.audit(orderId, 2, "材质不符");

        assertEquals(0, BigDecimal.ZERO.compareTo(balanceOf()));
    }

    @Test
    void repeatedAuditRejected() {
        Long orderId = doDeliveryAndGetOrderId();

        asTenant();
        deliveryOrderService.audit(orderId, 1, "通过");
        // 已审核再次审核被拒，杜绝重复入账
        assertThrows(BusinessException.class,
                () -> deliveryOrderService.audit(orderId, 1, "再次通过"));
        // 余额仅入账一次
        assertEquals(0, new BigDecimal("3.00").compareTo(balanceOf()));
    }

    @Test
    void applyWithdrawFreezesBalance() {
        seedBalance("10.00");

        asUser();
        WithdrawOrder order = walletService.applyWithdraw(userId, new BigDecimal("4.00"));

        assertEquals(0, order.getStatus());                              // 待审核
        assertEquals(0, new BigDecimal("6.00").compareTo(balanceOf()));  // 可用减少
        assertEquals(0, new BigDecimal("4.00").compareTo(pendingOf()));  // 待审核增加
    }

    @Test
    void auditApproveSettlesPending() {
        seedBalance("10.00");
        asUser();
        WithdrawOrder order = walletService.applyWithdraw(userId, new BigDecimal("4.00"));

        asTenant();
        walletService.auditWithdraw(order.getId(), true, "通过");

        assertEquals(0, new BigDecimal("6.00").compareTo(balanceOf()));  // 可用不变
        assertEquals(0, BigDecimal.ZERO.compareTo(pendingOf()));         // 待审核清减（资金转出）
    }

    @Test
    void auditRejectRefundsBalance() {
        seedBalance("10.00");
        asUser();
        WithdrawOrder order = walletService.applyWithdraw(userId, new BigDecimal("4.00"));

        asTenant();
        walletService.auditWithdraw(order.getId(), false, "驳回");

        assertEquals(0, new BigDecimal("10.00").compareTo(balanceOf())); // 全额退回
        assertEquals(0, BigDecimal.ZERO.compareTo(pendingOf()));
    }

    @Test
    void withdrawRejectedWhenInsufficientBalance() {
        seedBalance("1.00");
        asUser();
        assertThrows(BusinessException.class,
                () -> walletService.applyWithdraw(userId, new BigDecimal("5.00")));
    }
}
