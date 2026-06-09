package org.enveloping.ecobin.business.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

/**
 * 设备请求 COS STS 临时凭证
 */
@Data
public class StsTokenRequest {

    /** 设备序列号 */
    @NotBlank(message = "设备序列号不能为空")
    private String sn;

    /** 投口号 */
    @NotNull(message = "投口号不能为空")
    private Integer doorIndex;
}
