package org.enveloping.ecobin.business.controller;

import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.business.dto.CleanGrossRequest;
import org.enveloping.ecobin.business.dto.CleanTareRequest;
import org.enveloping.ecobin.business.service.CleanOrderService;
import org.enveloping.ecobin.common.result.Result;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 设备 IoT 清运上报接口。
 * <p>
 * 鉴权采用明文 SN 信任（无用户登录态）：服务层按上报的 {@code sn} 反查设备确定租户。
 * 路径在 SecurityConfig 中以 {@code /api/iot/**} 放行。
 * <ul>
 *   <li>{@code gross}（图④）：开清运门后设备先上报本次毛重，建清运记录，net = 毛重 - 该投口当前去皮；</li>
 *   <li>{@code tare}（图⑤）：清运员换新空袋后设备上报去皮，upsert 该投口当前垃圾袋编号与去皮。</li>
 * </ul>
 */
@RestController
@RequestMapping("/api/iot/clean")
@RequiredArgsConstructor
public class IotCleanController {

    private final CleanOrderService cleanOrderService;

    /** 清运毛重上报：建清运记录并计算实际清运量 */
    @PostMapping("/gross")
    public Result<Void> gross(@Valid @RequestBody CleanGrossRequest request) {
        cleanOrderService.reportGross(request);
        return Result.ok();
    }

    /** 换新空袋去皮上报：更新该投口当前垃圾袋去皮 */
    @PostMapping("/tare")
    public Result<Void> tare(@Valid @RequestBody CleanTareRequest request) {
        cleanOrderService.reportTare(request);
        return Result.ok();
    }
}
