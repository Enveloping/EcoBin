package org.enveloping.ecobin.business.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

/**
 * C 端清运员开清运门请求。
 * <p>
 * 清运员扫描新空垃圾袋二维码后调用：服务端校验投口/设备归属（本租户），下发开清运门指令并携带新垃圾袋编号。
 */
@Data
public class CleanOpenRequest {

    /** 投口ID（前端由本租户设备/投口列表选取） */
    @NotNull(message = "投口ID不能为空")
    private Long doorId;

    /** 本次清运绑定的新垃圾袋编号（扫码获取） */
    @NotBlank(message = "垃圾袋编号不能为空")
    private String bagNo;
}
