package org.enveloping.ecobin.business.dto;

import jakarta.validation.constraints.NotNull;
import lombok.Data;

/**
 * C 端用户开投口请求（投递两阶段·阶段1）。
 */
@Data
public class OpenDoorRequest {

    /** 投口ID（前端由设备投口列表选取） */
    @NotNull(message = "投口ID不能为空")
    private Long doorId;
}
