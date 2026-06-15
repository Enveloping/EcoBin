package org.enveloping.ecobin.business.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

import java.math.BigDecimal;

/**
 * 设备 IoT 去皮上报请求（图⑤：清运员换上新空袋后设备上报去皮重量）。
 * <p>
 * 设备只回传 {@code cleanOrderId} + 新空袋去皮重；新袋编号 open 时已由小程序扫到并记在订单上，设备不碰 bagNo。
 * 后端按订单取 {@code deviceId/doorIndex/userId/newBagQr}，upsert 该投口当前垃圾袋编号与去皮重量。
 * 鉴权采用明文 SN 信任（OneNet 链路 sn 由报文头给）。
 */
@Data
public class CleanTareRequest {

    /** 设备序列号 */
    @NotBlank(message = "设备序列号不能为空")
    private String sn;

    /** 清运订单ID（开门时下发，设备原样回传） */
    @NotNull(message = "清运订单ID不能为空")
    private Long cleanOrderId;

    /** 新空袋去皮重量（kg） */
    @NotNull(message = "去皮重量不能为空")
    private BigDecimal weight;
}
