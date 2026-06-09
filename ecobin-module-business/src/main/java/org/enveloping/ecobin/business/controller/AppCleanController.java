package org.enveloping.ecobin.business.controller;

import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.business.dto.CleanOpenRequest;
import org.enveloping.ecobin.business.entity.CleanOrder;
import org.enveloping.ecobin.business.service.CleanOrderService;
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
 * 小程序终端用户 C 端接口：清运作业（清运员 / 设备管理员）。
 * <p>
 * 清运员扫新空垃圾袋后调用 {@code open} 开清运门（下发指令携带新袋编号）；本次毛重与换袋去皮由设备经
 * {@code /api/iot/clean/**} 上报。查询限本人：{@code userId} 取登录态，租户拦截器叠加 {@code tenant_id} 隔离。
 * 角色边界由 SecurityConfig 限定 CLEANER/DEVICE_ADMIN（普通用户 USER 不可清运）。
 */
@RestController
@RequestMapping("/api/app/clean")
@RequiredArgsConstructor
public class AppCleanController {

    private final CleanOrderService cleanOrderService;

    /** 开清运门：扫新空垃圾袋后下发开门指令（携带新袋编号） */
    @PostMapping("/open")
    public Result<Void> open(@Valid @RequestBody CleanOpenRequest request) {
        cleanOrderService.openCleanDoor(request.getDoorId(), request.getBagNo());
        return Result.ok();
    }

    /** 我的清运记录分页 */
    @GetMapping("/my")
    public Result<PageResult<CleanOrder>> myOrders(@RequestParam(defaultValue = "1") int page,
                                                   @RequestParam(defaultValue = "20") int pageSize) {
        Long userId = SecurityUtils.getCurrentUserId();
        return Result.ok(cleanOrderService.pageMyOrders(userId, page, pageSize));
    }

    /** 我的单条清运详情 */
    @GetMapping("/my/{id}")
    public Result<CleanOrder> myOrder(@PathVariable Long id) {
        Long userId = SecurityUtils.getCurrentUserId();
        return Result.ok(cleanOrderService.getMyOrder(userId, id));
    }
}
