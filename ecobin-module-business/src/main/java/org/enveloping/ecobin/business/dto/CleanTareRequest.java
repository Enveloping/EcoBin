package org.enveloping.ecobin.business.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

import java.math.BigDecimal;

/**
 * 设备 IoT 去皮上报请求（图⑤：清运员换上新空袋后设备上报去皮重量）。
 * <p>
 * 鉴权采用明文 SN 信任：后端按 {@code sn} 反查设备并据此确定租户；upsert 该投口当前垃圾袋编号与去皮重量。
 */
@Data
public class CleanTareRequest {

    /** 设备序列号 */
    @NotBlank(message = "设备序列号不能为空")
    private String sn;

    /** 投口号（物理编号，第几个投口） */
    @NotNull(message = "投口号不能为空")
    private Integer doorIndex;

    /** 清运人ID（设备上当前登录清运员） */
    @NotNull(message = "清运人ID不能为空")
    private Long userId;

    /** 新垃圾袋编号 */
    @NotBlank(message = "垃圾袋编号不能为空")
    private String bagNo;

    /** 新空袋去皮重量（kg） */
    @NotNull(message = "去皮重量不能为空")
    private BigDecimal weight;
}
