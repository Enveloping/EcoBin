package org.enveloping.ecobin.device.controller;

import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.common.result.Result;
import org.enveloping.ecobin.framework.cos.CosStsCredential;
import org.enveloping.ecobin.framework.cos.CosTokenClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 设备直传 COS 的临时密钥拉取接口（开发/联调用）。
 * <p>
 * 路径在 {@code SecurityConfig} 中以 {@code /api/iot/**} 放行，无需 JWT。
 * <p>
 * 注意：正式投递/清运流程里，临时密钥是随「开门命令」的 {@code cosToken} 由 OneNet
 * <strong>下发</strong>给设备的（见 {@code docs/onenet-thing-model.md} §3.4），设备不必主动拉。
 * 本端点是为「设备侧脚本联调 / 密钥中途刷新」提供的主动拉取通道，复用同一份
 * {@link CosTokenClient#getTempCredentials}，凭证未配置时返回占位值。
 */
@RestController
@RequestMapping("/api/iot/cos")
@RequiredArgsConstructor
public class IotCosController {

    private final CosTokenClient cosTokenClient;

    /**
     * 取一份 STS 临时密钥（含 bucket/region/baseUrl）。
     *
     * @param sn        设备序列号（可选，仅用于日志留痕）
     * @param doorIndex 投口号（可选，仅用于日志留痕）
     */
    @GetMapping("/temp-credentials")
    public Result<CosStsCredential> tempCredentials(
            @RequestParam(required = false) String sn,
            @RequestParam(required = false, defaultValue = "0") Integer doorIndex) {
        return Result.ok(cosTokenClient.getTempCredentials(sn, doorIndex));
    }
}
