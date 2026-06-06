package org.enveloping.ecobin.business.controller;

import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.business.entity.DeliveryOrder;
import org.enveloping.ecobin.business.service.DeliveryOrderService;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.common.result.Result;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/api/business/delivery")
@RequiredArgsConstructor
public class DeliveryOrderController {

    private final DeliveryOrderService deliveryOrderService;

    @GetMapping
    public Result<PageResult<DeliveryOrder>> page(@RequestParam(defaultValue = "1") int page,
                                                    @RequestParam(defaultValue = "20") int pageSize) {
        return Result.ok(deliveryOrderService.pageOrders(page, pageSize));
    }

    @GetMapping("/{id}")
    public Result<DeliveryOrder> get(@PathVariable Long id) {
        return Result.ok(deliveryOrderService.getById(id));
    }

    @PostMapping
    public Result<DeliveryOrder> create(@RequestBody DeliveryOrder order) {
        deliveryOrderService.save(order);
        return Result.ok(order);
    }

    @GetMapping("/today-overview")
    public Result<Map<String, Object>> todayOverview() {
        return Result.ok(deliveryOrderService.todayOverview());
    }
}
