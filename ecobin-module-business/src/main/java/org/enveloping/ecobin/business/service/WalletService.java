package org.enveloping.ecobin.business.service;

import org.enveloping.ecobin.business.entity.WithdrawOrder;
import org.enveloping.ecobin.common.result.PageResult;

import java.math.BigDecimal;

/**
 * 用户钱包与提现服务。
 * <p>
 * 余额挂在 {@code sys_user}（balance 可用 / pending_balance 待审核）；
 * 资金流：投递返现入账 → 用户发起提现（冻结）→ 租户审核（通过结算 / 驳回退回）。
 */
public interface WalletService {

    /**
     * 投递返现入账：增加用户可用余额（投递完成时调用）。
     * {@code userId} 为空、{@code amount} 非正时静默跳过。
     */
    void income(Long userId, Long tenantId, BigDecimal amount, Long deliveryOrderId);

    /**
     * 发起提现：校验并冻结可用余额（转入待审核），创建待审核提现单。
     *
     * @return 新建的提现单
     */
    WithdrawOrder applyWithdraw(Long userId, BigDecimal amount);

    /**
     * 租户审核提现单。通过：扣减待审核余额（资金转出，真实转账待接入）；驳回：待审核退回可用。
     */
    void auditWithdraw(Long withdrawId, boolean pass, String remark);

    /** 我的提现记录分页 */
    PageResult<WithdrawOrder> pageMyWithdraws(Long userId, int page, int pageSize);

    /** 租户后台提现单分页（按状态可选过滤；租户拦截器自动隔离本租户） */
    PageResult<WithdrawOrder> pageTenantWithdraws(Integer status, int page, int pageSize);
}
