package org.enveloping.ecobin.business.entity;

import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;
import org.enveloping.ecobin.common.base.BaseEntity;

import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * 提现申请单实体。
 * <p>
 * 资金流：用户发起提现 → 可用余额转入待审核余额（冻结）→ 租户后台审核
 * （通过：扣减待审核 + 占位转账；驳回：待审核退回可用）。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("biz_withdraw_order")
public class WithdrawOrder extends BaseEntity {

    /** 申请用户ID */
    private Long userId;

    /** 提现金额 */
    private BigDecimal amount;

    /** 状态：0-待审核 1-已通过 2-已驳回 */
    private Integer status;

    /** 审核租户主体ID */
    private Long auditBy;

    /** 审核时间 */
    private LocalDateTime auditTime;

    /** 审核备注 */
    private String auditRemark;

    /** 微信转账单号（真实转账接入后回填） */
    private String transferNo;
}
