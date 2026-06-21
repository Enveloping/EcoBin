package org.enveloping.ecobin.business.dto;

import jakarta.validation.constraints.NotNull;
import lombok.Data;

/**
 * 投递订单审核请求
 */
@Data
public class DeliveryAuditRequest {

    /** 审核状态：1-审核通过 2-审核拒绝 */
    @NotNull(message = "审核状态不能为空")
    private Integer auditStatus;

    /** 审核备注（可选） */
    private String remark;
}
