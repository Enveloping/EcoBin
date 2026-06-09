package org.enveloping.ecobin.business.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

/**
 * 设备直传 COS 完成后回报照片 URL。
 * <p>
 * 4 个字段均可空，支持设备逐张异步上传后再调一次补齐。
 */
@Data
public class PhotoNotifyRequest {

    /** 关联订单号 */
    @NotBlank(message = "订单号不能为空")
    private String orderSn;

    /** 订单类型：1-投递 2-清运 */
    @NotNull(message = "订单类型不能为空")
    private Integer orderType;

    /** 开门前·箱外照片 URL */
    private String photoOpenOutside;

    /** 开门前·箱内照片 URL */
    private String photoOpenInside;

    /** 关门后·箱外照片 URL */
    private String photoCloseOutside;

    /** 关门后·箱内照片 URL */
    private String photoCloseInside;
}
