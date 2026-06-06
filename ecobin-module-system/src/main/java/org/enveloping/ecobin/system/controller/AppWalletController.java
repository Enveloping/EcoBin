package org.enveloping.ecobin.system.controller;

import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.common.exception.BusinessException;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.common.result.Result;
import org.enveloping.ecobin.framework.security.SecurityUtils;
import org.enveloping.ecobin.system.dto.WalletVO;
import org.enveloping.ecobin.system.dto.WithdrawApplyRequest;
import org.enveloping.ecobin.system.entity.User;
import org.enveloping.ecobin.system.entity.WithdrawOrder;
import org.enveloping.ecobin.system.service.UserService;
import org.enveloping.ecobin.system.service.WalletService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 小程序终端用户 C 端接口：钱包（余额 + 提现）。
 * <p>
 * 余额来自 {@code sys_user}（投递返现累计）；均限本人：以登录态 userId 为准，租户拦截器叠加隔离。
 */
@RestController
@RequestMapping("/api/app/wallet")
@RequiredArgsConstructor
public class AppWalletController {

    private final UserService userService;
    private final WalletService walletService;

    /** 我的钱包余额 */
    @GetMapping
    public Result<WalletVO> myWallet() {
        Long userId = SecurityUtils.getCurrentUserId();
        User user = userService.getById(userId);
        if (user == null) {
            throw new BusinessException(404, "用户不存在");
        }
        return Result.ok(WalletVO.from(user));
    }

    /** 发起提现 */
    @PostMapping("/withdraw")
    public Result<WithdrawOrder> applyWithdraw(@Valid @RequestBody WithdrawApplyRequest request) {
        Long userId = SecurityUtils.getCurrentUserId();
        return Result.ok(walletService.applyWithdraw(userId, request.getAmount()));
    }

    /** 我的提现记录分页 */
    @GetMapping("/withdraw")
    public Result<PageResult<WithdrawOrder>> myWithdraws(@RequestParam(defaultValue = "1") int page,
                                                         @RequestParam(defaultValue = "20") int pageSize) {
        Long userId = SecurityUtils.getCurrentUserId();
        return Result.ok(walletService.pageMyWithdraws(userId, page, pageSize));
    }
}
