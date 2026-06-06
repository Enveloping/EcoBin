package org.enveloping.ecobin.business.controller;

import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.business.dto.DeliveryReportRequest;
import org.enveloping.ecobin.business.service.DeliveryOrderService;
import org.enveloping.ecobin.common.result.Result;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 设备 IoT 上报接口（投递两阶段·阶段2）。
 * <p>
 * 鉴权采用明文 SN 信任（无用户登录态）：服务层按上报的 {@code sn} 反查设备确定租户，
 * 并校验投递记录确属该设备/租户。路径在 SecurityConfig 中以 {@code /api/iot/**} 放行。
 */
@RestController
@RequestMapping("/api/iot/delivery")
@RequiredArgsConstructor
public class IotDeliveryController {

    private final DeliveryOrderService deliveryOrderService;

    /** 投递完成上报：按投递标识符关联回填重量并置为已完成 */
    @PostMapping("/complete")
    public Result<Void> complete(@Valid @RequestBody DeliveryReportRequest request) {
        deliveryOrderService.completeDelivery(request);
        return Result.ok();
    }
}
