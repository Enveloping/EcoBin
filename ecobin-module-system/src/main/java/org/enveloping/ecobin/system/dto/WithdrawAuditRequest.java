package org.enveloping.ecobin.system.dto;

import lombok.Data;

/**
 * 租户后台提现审核请求。
 */
@Data
public class WithdrawAuditRequest {

    /** 是否通过：true-通过 false-驳回 */
    private boolean pass;

    /** 审核备注 */
    private String remark;
}
