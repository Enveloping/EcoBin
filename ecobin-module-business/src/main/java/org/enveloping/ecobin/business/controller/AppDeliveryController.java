package org.enveloping.ecobin.business.controller;

import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.business.dto.OpenDoorRequest;
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
 * 投递流程：用户在小程序「开启设备」（本控制器）记录该设备「当前活跃用户」会话并下发开投口指令；
 * 设备投放称重后上报，后端此刻才建单（{@link IotDeliveryController}，上传后建单，归属取活跃会话）。
 * 用户可在设备屏上「继续投递」再开门，仍归同一活跃用户。
 * 查询仅返回属于自己的记录：租户拦截器自动注入 {@code tenant_id}，再叠加 {@code user_id} 归属过滤。
 */
@RestController
@RequestMapping("/api/app/delivery")
@RequiredArgsConstructor
public class AppDeliveryController {

    private final DeliveryOrderService deliveryOrderService;

    /** 开启设备：记录「当前活跃用户」会话并下发开投口指令（不建单，订单在设备上传后生成） */
    @PostMapping("/open")
    public Result<Void> openDoor(@Valid @RequestBody OpenDoorRequest request) {
        deliveryOrderService.openDoor(request.getDoorId());
        return Result.ok();
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
