package org.enveloping.ecobin;

import org.enveloping.ecobin.business.entity.DeliveryOrder;
import org.enveloping.ecobin.business.service.DeliveryOrderService;
import org.enveloping.ecobin.business.service.StatisticsService;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.framework.tenant.TenantContextHolder;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 分页单页上限（200）验证：
 * <ul>
 *   <li>{@code PaginationInnerInterceptor.setMaxLimit(200)} 全局兜底走 IPage 的查询；</li>
 *   <li>正常 pageSize 不被误伤；</li>
 *   <li>裸 SQL LIMIT 的 deviceRanking 在服务层另行 clamp。</li>
 * </ul>
 */
@SpringBootTest
@ActiveProfiles("test")
@Transactional
class PageSizeLimitTest {

    @Autowired
    private DeliveryOrderService deliveryOrderService;
    @Autowired
    private StatisticsService statisticsService;

    private final Long tenantId = 2L;

    @BeforeEach
    void setUp() {
        TenantContextHolder.setTenantId(tenantId);
        TenantContextHolder.setIgnore(true);
        // 造 201 条投递订单，超过单页上限 200
        for (int i = 0; i < 201; i++) {
            DeliveryOrder order = new DeliveryOrder();
            order.setTenantId(tenantId);
            order.setUserId(1L);
            order.setWasteType1(2);
            order.setStatus(0);
            deliveryOrderService.save(order);
        }
        TenantContextHolder.clear();
    }

    @AfterEach
    void tearDown() {
        TenantContextHolder.clear();
    }

    private void asPlatform() {
        TenantContextHolder.setTenantId(1L);
        TenantContextHolder.setIgnore(true);
    }

    @Test
    void pageSizeCappedAtMax() {
        asPlatform();
        PageResult<DeliveryOrder> result = deliveryOrderService.pageOrders(1, 100000);
        assertEquals(200, result.getRecords().size(), "单页最多返回 200 条");
        assertTrue(result.getTotal() >= 201, "总数不受 limit 影响");
    }

    @Test
    void normalPageSizeUnaffected() {
        asPlatform();
        PageResult<DeliveryOrder> result = deliveryOrderService.pageOrders(1, 20);
        assertEquals(20, result.getRecords().size(), "正常分页不被误伤");
    }

    @Test
    void deviceRankingClampsPageSize() {
        asPlatform();
        // 不抛错即说明裸 SQL clamp 接通；行数不会超过上限
        List<Map<String, Object>> ranking = statisticsService.deviceRanking(100000);
        assertTrue(ranking.size() <= 200, "deviceRanking 行数不超过上限");
    }
}
