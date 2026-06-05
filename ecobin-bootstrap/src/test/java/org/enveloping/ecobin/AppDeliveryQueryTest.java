package org.enveloping.ecobin;

import org.enveloping.ecobin.business.entity.DeliveryOrder;
import org.enveloping.ecobin.business.service.DeliveryOrderService;
import org.enveloping.ecobin.common.exception.BusinessException;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.framework.tenant.TenantContextHolder;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

/**
 * 验证 C 端「我的投递记录」按 user_id 的归属过滤：
 * 同一租户下，用户只能分页/查看属于自己的投递订单，越权读他人订单按"不存在"处理。
 */
@SpringBootTest
@ActiveProfiles("test")
class AppDeliveryQueryTest {

    @Autowired
    private DeliveryOrderService deliveryOrderService;

    @AfterEach
    void tearDown() {
        TenantContextHolder.clear();
    }

    @Test
    void myOrdersAreScopedToCurrentUser() {
        // 租户2 上下文：用户10、用户11 各一条投递订单
        TenantContextHolder.setTenantId(2L);
        TenantContextHolder.setIgnore(false);

        DeliveryOrder o10 = newOrder(2L, 10L);
        deliveryOrderService.save(o10);
        DeliveryOrder o11 = newOrder(2L, 11L);
        deliveryOrderService.save(o11);

        // 用户10 分页：只看到自己的那条
        PageResult<DeliveryOrder> page = deliveryOrderService.pageMyOrders(10L, 1, 20);
        assertEquals(1, page.getTotal(), "用户10 应只看到自己的投递记录");
        assertEquals(10L, page.getRecords().get(0).getUserId());

        // 用户10 读自己的订单成功
        DeliveryOrder mine = deliveryOrderService.getMyOrder(10L, o10.getId());
        assertEquals(10L, mine.getUserId());

        // 用户10 读用户11 的订单 → 业务异常（不暴露存在性）
        assertThrows(BusinessException.class,
                () -> deliveryOrderService.getMyOrder(10L, o11.getId()),
                "越权读他人订单应抛业务异常");
    }

    private DeliveryOrder newOrder(Long tenantId, Long userId) {
        DeliveryOrder order = new DeliveryOrder();
        order.setTenantId(tenantId);
        order.setUserId(userId);
        order.setWasteType1(1);
        order.setStatus(0);
        return order;
    }
}
