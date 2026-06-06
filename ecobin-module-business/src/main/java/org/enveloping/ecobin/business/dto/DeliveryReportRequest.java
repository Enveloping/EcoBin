package org.enveloping.ecobin.business.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

import java.math.BigDecimal;

/**
 * 设备 IoT 投递完成上报请求（投递两阶段·阶段2）。
 * <p>
 * 鉴权采用明文 SN 信任：后端按 {@code sn} 反查设备并据此确定租户。
 */
@Data
public class DeliveryReportRequest {

    /** 设备序列号 */
    @NotBlank(message = "设备序列号不能为空")
    private String sn;

    /** 投递标识符（开投口时由后端生成下发，此处原样带回） */
    @NotBlank(message = "投递标识符不能为空")
    private String deliveryToken;

    /** 本次投递重量（kg） */
    @NotNull(message = "投递重量不能为空")
    private BigDecimal weight;

    /** 一级分类（可选，缺省沿用开投口时投口配置） */
    private Integer wasteType1;

    /** 二级分类（可选） */
    private Integer wasteType2;
}
