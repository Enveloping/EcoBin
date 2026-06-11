package org.enveloping.ecobin.business.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

import java.math.BigDecimal;

/**
 * 设备 IoT 清运毛重上报请求（图④：开清运门后设备先上报本次毛重）。
 * <p>
 * 设备只回传开门时下发的 {@code cleanOrderId}（业务标识符）+ 本次毛重；{@code userId / doorIndex / 去皮}
 * 全部由后端按订单反查，设备不再携带用户/投口信息。{@code cleanOrderId} 同时充当幂等键（一单一次毛重）。
 * 鉴权采用明文 SN 信任：后端按 {@code sn} 反查设备并校验订单归属（OneNet 链路 sn 由报文头给）。
 */
@Data
public class CleanGrossRequest {

    /** 设备序列号 */
    @NotBlank(message = "设备序列号不能为空")
    private String sn;

    /** 清运订单ID（开门时下发，设备原样回传） */
    @NotNull(message = "清运订单ID不能为空")
    private Long cleanOrderId;

    /** 本次清运毛重（kg，满袋重量） */
    @NotNull(message = "清运毛重不能为空")
    private BigDecimal weight;
}
