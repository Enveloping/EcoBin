package org.enveloping.ecobin.business.controller;

import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.business.dto.CleanSubmitRequest;
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
 * 清运员选取本租户设备/投口（来自 {@code GET /api/app/device}）后提交清理重量，单据建为待审核，
 * 由租户后台审核（{@code PUT /api/business/clean/{id}/audit}）。提交与查询均限本人：
 * {@code userId} 取登录态，租户拦截器叠加 {@code tenant_id} 隔离。
 * 角色边界由 SecurityConfig 限定 CLEANER/DEVICE_ADMIN（普通用户 USER 不可清运）。
 */
@RestController
@RequestMapping("/api/app/clean")
@RequiredArgsConstructor
public class AppCleanController {

    private final CleanOrderService cleanOrderService;

    /** 提交清运 */
    @PostMapping
    public Result<CleanOrder> submit(@Valid @RequestBody CleanSubmitRequest request) {
        Long userId = SecurityUtils.getCurrentUserId();
        return Result.ok(cleanOrderService.submitClean(userId, request));
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
