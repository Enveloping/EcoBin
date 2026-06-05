package org.enveloping.ecobin.business.controller;

import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.business.entity.DeliveryOrder;
import org.enveloping.ecobin.business.service.DeliveryOrderService;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.common.result.Result;
import org.enveloping.ecobin.framework.security.SecurityUtils;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 小程序终端用户 C 端接口：投递记录（只读，仅本人）。
 * <p>
 * 投递订单由设备/物联网侧上报生成，终端用户仅查询属于自己的记录。
 * 数据隔离：租户拦截器自动注入 {@code tenant_id}，再叠加 {@code user_id} 归属过滤。
 */
@RestController
@RequestMapping("/api/app/delivery")
@RequiredArgsConstructor
public class AppDeliveryController {

    private final DeliveryOrderService deliveryOrderService;

    /** 我的投递记录分页 */
    @GetMapping("/my")
    public Result<PageResult<DeliveryOrder>> myOrders(@RequestParam(defaultValue = "1") int page,
                                                      @RequestParam(defaultValue = "20") int pageSize) {
        Long userId = SecurityUtils.getCurrentUserId();
        return Result.ok(deliveryOrderService.pageMyOrders(userId, page, pageSize));
    }

    /** 我的单条投递详情 */
    @GetMapping("/my/{id}")
    public Result<DeliveryOrder> myOrder(@PathVariable Long id) {
        Long userId = SecurityUtils.getCurrentUserId();
        return Result.ok(deliveryOrderService.getMyOrder(userId, id));
    }
}
