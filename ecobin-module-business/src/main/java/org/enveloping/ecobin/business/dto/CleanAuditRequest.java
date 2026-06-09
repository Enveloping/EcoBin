package org.enveloping.ecobin.business.dto;

import jakarta.validation.constraints.NotNull;
import lombok.Data;

/**
 * 清运单审核请求
 */
@Data
public class CleanAuditRequest {

    /** 审核状态：0-待审核 1-审核通过 2-审核拒绝 */
    @NotNull(message = "审核状态不能为空")
    private Integer auditStatus;
}
