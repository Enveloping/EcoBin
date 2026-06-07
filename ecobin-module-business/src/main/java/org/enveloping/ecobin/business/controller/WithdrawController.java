package org.enveloping.ecobin.business.controller;

import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.business.dto.WithdrawAuditRequest;
import org.enveloping.ecobin.business.entity.WithdrawOrder;
import org.enveloping.ecobin.business.service.WalletService;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.common.result.Result;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 租户后台：提现单审核。
 * <p>
 * 列表与审核均受租户拦截器隔离，租户仅能处理本租户用户的提现单。
 */
@RestController
@RequestMapping("/api/system/withdraw")
@RequiredArgsConstructor
public class WithdrawController {

    private final WalletService walletService;

    /** 提现单列表（status 可选：0-待审核 1-已通过 2-已驳回） */
    @GetMapping
    public Result<PageResult<WithdrawOrder>> list(@RequestParam(required = false) Integer status,
                                                  @RequestParam(defaultValue = "1") int page,
                                                  @RequestParam(defaultValue = "20") int pageSize) {
        return Result.ok(walletService.pageTenantWithdraws(status, page, pageSize));
    }

    /** 审核提现单（通过/驳回） */
    @PostMapping("/{id}/audit")
    public Result<Void> audit(@PathVariable Long id, @Valid @RequestBody WithdrawAuditRequest request) {
        walletService.auditWithdraw(id, request.isPass(), request.getRemark());
        return Result.ok();
    }
}
