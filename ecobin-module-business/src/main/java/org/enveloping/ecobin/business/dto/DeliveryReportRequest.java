package org.enveloping.ecobin.business.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

import java.math.BigDecimal;

/**
 * 设备 IoT 投递完成上报请求（投递·上传后建单）。
 * <p>
 * 鉴权采用明文 SN 信任：后端按 {@code sn} 反查设备并据此确定租户。
 * 投递改为「上传后建单」：后端收到本上报时才建单，用户归属取该设备「当前活跃用户」会话。
 */
@Data
public class DeliveryReportRequest {

    /** 设备序列号 */
    @NotBlank(message = "设备序列号不能为空")
    private String sn;

    /** 投口号（设备上报开的哪个投口；后端据 device+doorIndex 反查投口取单价/分类） */
    @NotNull(message = "投口号不能为空")
    private Integer doorIndex;

    /**
     * 幂等键（OneNet 消息 id / MQ messageId，由分发器注入；直连上报可由设备自带）。
     * 后端按 device+msgId 去重，落库到 {@code delivery_token} 列。为空则不去重。
     */
    private String msgId;

    /** 本次投递重量（kg） */
    @NotNull(message = "投递重量不能为空")
    private BigDecimal weight;

    /** 一级分类（可选，缺省沿用开投口时投口配置） */
    private Integer wasteType1;

    /** 二级分类（可选） */
    private Integer wasteType2;

    // ---------- 照片 URL（设备直传 COS 后随本次称重上报一并回传，后端原样存；缺失则前端占位） ----------
    // 投递「上传后建单 + 设备本地继续投递」，开门时后端无订单标识，故照片位置由设备决定并回传，不由后端复原。

    /** 开门前·箱外照片 URL（可选） */
    private String photoOpenOutside;

    /** 开门前·箱内照片 URL（可选） */
    private String photoOpenInside;

    /** 关门后·箱外照片 URL（可选） */
    private String photoCloseOutside;

    /** 关门后·箱内照片 URL（可选） */
    private String photoCloseInside;
}
