package org.enveloping.ecobin.business.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.business.entity.WithdrawOrder;
import org.enveloping.ecobin.business.mapper.WithdrawOrderMapper;
import org.enveloping.ecobin.business.service.WalletService;
import org.enveloping.ecobin.common.exception.BusinessException;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.framework.security.SecurityUtils;
import org.enveloping.ecobin.system.mapper.UserMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.LocalDateTime;

@Service
@RequiredArgsConstructor
public class WalletServiceImpl implements WalletService {

    private final UserMapper userMapper;
    private final WithdrawOrderMapper withdrawOrderMapper;

    private static final int STATUS_PENDING = 0;
    private static final int STATUS_APPROVED = 1;
    private static final int STATUS_REJECTED = 2;

    @Override
    @Transactional
    public void income(Long userId, Long tenantId, BigDecimal amount, Long deliveryOrderId) {
        if (userId == null || amount == null || amount.signum() <= 0) {
            return;
        }
        userMapper.addBalance(userId, amount);
    }

    @Override
    @Transactional
    public WithdrawOrder applyWithdraw(Long userId, BigDecimal amount) {
        if (amount == null || amount.signum() <= 0) {
            throw new BusinessException(400, "提现金额必须大于0");
        }
        // 原子冻结：余额不足时条件 UPDATE 不命中，返回 0
        if (userMapper.freezeForWithdraw(userId, amount) == 0) {
            throw new BusinessException(400, "余额不足");
        }
        WithdrawOrder order = new WithdrawOrder();
        order.setUserId(userId);
        order.setAmount(amount);
        order.setStatus(STATUS_PENDING);
        withdrawOrderMapper.insert(order);   // tenant_id 由租户拦截器按当前登录用户注入
        return order;
    }

    @Override
    @Transactional
    public void auditWithdraw(Long withdrawId, boolean pass, String remark) {
        // 租户拦截器隔离：仅能查到/审核本租户的提现单
        WithdrawOrder order = withdrawOrderMapper.selectById(withdrawId);
        if (order == null) {
            throw new BusinessException(404, "提现单不存在");
        }
        if (order.getStatus() == null || order.getStatus() != STATUS_PENDING) {
            throw new BusinessException(400, "提现单已审核，请勿重复操作");
        }
        if (pass) {
            userMapper.settlePending(order.getUserId(), order.getAmount());
            order.setStatus(STATUS_APPROVED);
            // TODO: 调微信「商家转账到零钱」（付款方=租户商户号 merchant_no，收款方=用户 openid），
            //       转账成功后回填 order.transferNo；当前仅标记已通过，真实转账待 IoT/支付网关接入。
        } else {
            userMapper.refundPending(order.getUserId(), order.getAmount());
            order.setStatus(STATUS_REJECTED);
        }
        order.setAuditBy(SecurityUtils.getCurrentUserId());
        order.setAuditTime(LocalDateTime.now());
        order.setAuditRemark(remark);
        withdrawOrderMapper.updateById(order);
    }

    @Override
    public PageResult<WithdrawOrder> pageMyWithdraws(Long userId, int page, int pageSize) {
        Page<WithdrawOrder> p = new Page<>(page, pageSize);
        Page<WithdrawOrder> result = withdrawOrderMapper.selectPage(p,
                new LambdaQueryWrapper<WithdrawOrder>()
                        .eq(WithdrawOrder::getUserId, userId)
                        .orderByDesc(WithdrawOrder::getCreateTime));
        return PageResult.of(result.getRecords(), result.getTotal(), page, pageSize);
    }

    @Override
    public PageResult<WithdrawOrder> pageTenantWithdraws(Integer status, int page, int pageSize) {
        Page<WithdrawOrder> p = new Page<>(page, pageSize);
        Page<WithdrawOrder> result = withdrawOrderMapper.selectPage(p,
                new LambdaQueryWrapper<WithdrawOrder>()
                        .eq(status != null, WithdrawOrder::getStatus, status)
                        .orderByDesc(WithdrawOrder::getCreateTime));
        return PageResult.of(result.getRecords(), result.getTotal(), page, pageSize);
    }
}
