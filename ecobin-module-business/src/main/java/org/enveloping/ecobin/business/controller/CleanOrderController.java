package org.enveloping.ecobin.business.controller;

import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.business.entity.CleanOrder;
import org.enveloping.ecobin.business.service.CleanOrderService;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.common.result.Result;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/business/clean")
@RequiredArgsConstructor
public class CleanOrderController {

    private final CleanOrderService cleanOrderService;

    @GetMapping
    public Result<PageResult<CleanOrder>> page(@RequestParam(defaultValue = "1") int page,
                                                @RequestParam(defaultValue = "20") int pageSize) {
        return Result.ok(cleanOrderService.pageOrders(page, pageSize));
    }

    @GetMapping("/{id}")
    public Result<CleanOrder> get(@PathVariable Long id) {
        return Result.ok(cleanOrderService.getById(id));
    }

    @PostMapping
    public Result<CleanOrder> create(@RequestBody CleanOrder order) {
        cleanOrderService.save(order);
        return Result.ok(order);
    }

    @PutMapping("/{id}")
    public Result<CleanOrder> update(@PathVariable Long id, @RequestBody CleanOrder order) {
        order.setId(id);
        cleanOrderService.updateById(order);
        return Result.ok(cleanOrderService.getById(id));
    }

    @DeleteMapping("/{id}")
    public Result<Void> delete(@PathVariable Long id) {
        cleanOrderService.removeById(id);
        return Result.ok();
    }
}
