package org.enveloping.ecobin.business.dto;

import jakarta.validation.constraints.NotNull;
import lombok.Data;

import java.math.BigDecimal;

/**
 * C 端清运员提交清运请求。
 * <p>
 * 清运员在小程序选取目标设备/投口（来自 {@code GET /api/app/device}），上报本次清理重量。
 * 服务端以登录态 userId 锁定清运员，{@code tenant_id} 由设备归属确定，单据建为待审核（auditStatus=0）。
 */
@Data
public class CleanSubmitRequest {

    /** 设备ID（前端由本租户设备列表选取） */
    @NotNull(message = "设备ID不能为空")
    private Long deviceId;

    /** 投口ID（可选，按投口清运时指定） */
    private Long doorId;

    /** 一级分类 */
    @NotNull(message = "一级分类不能为空")
    private Integer wasteType1;

    /** 二级分类（可选，缺省 0-不区分） */
    private Integer wasteType2;

    /** 本次清理重量（kg） */
    @NotNull(message = "清理重量不能为空")
    private BigDecimal weight;
}
