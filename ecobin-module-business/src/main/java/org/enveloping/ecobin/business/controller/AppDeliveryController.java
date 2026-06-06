package org.enveloping.ecobin.business.controller;

import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.business.dto.OpenDoorRequest;
import org.enveloping.ecobin.business.dto.OpenDoorResult;
import org.enveloping.ecobin.business.entity.DeliveryOrder;
import org.enveloping.ecobin.business.service.DeliveryOrderService;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.common.result.Result;
import org.enveloping.ecobin.framework.security.SecurityUtils;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 小程序终端用户 C 端接口：投递。
 * <p>
 * 投递采用两阶段流程：用户在小程序「开投口」（阶段1，本控制器）创建进行中记录并下发开投口指令；
 * 设备在用户关投口后回传重量等完成上报（阶段2，{@link IotDeliveryController}）。
 * 查询仅返回属于自己的记录：租户拦截器自动注入 {@code tenant_id}，再叠加 {@code user_id} 归属过滤。
 */
@RestController
@RequestMapping("/api/app/delivery")
@RequiredArgsConstructor
public class AppDeliveryController {

    private final DeliveryOrderService deliveryOrderService;

    /** 开投口（投递两阶段·阶段1）：创建进行中投递记录，返回投递标识符 */
    @PostMapping("/open")
    public Result<OpenDoorResult> openDoor(@Valid @RequestBody OpenDoorRequest request) {
        return Result.ok(deliveryOrderService.openDoor(request.getDoorId()));
    }

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
