package org.enveloping.ecobin.business.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

import java.math.BigDecimal;

/**
 * 设备 IoT 清运毛重上报请求（图④：开清运门后设备先上报本次毛重）。
 * <p>
 * 鉴权采用明文 SN 信任：后端按 {@code sn} 反查设备并据此确定租户；{@code doorIndex} 为物理投口号。
 */
@Data
public class CleanGrossRequest {

    /** 设备序列号 */
    @NotBlank(message = "设备序列号不能为空")
    private String sn;

    /** 投口号（物理编号，第几个投口） */
    @NotNull(message = "投口号不能为空")
    private Integer doorIndex;

    /** 清运人ID（设备上当前登录清运员） */
    @NotNull(message = "清运人ID不能为空")
    private Long userId;

    /** 本次清运毛重（kg，满袋重量） */
    @NotNull(message = "清运毛重不能为空")
    private BigDecimal weight;

    /** 上报订单号（幂等键，可选；设备会重复上报直到成功，相同 reportSn 不重复建单） */
    private String reportSn;
}
