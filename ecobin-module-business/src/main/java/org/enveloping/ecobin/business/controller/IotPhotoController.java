package org.enveloping.ecobin.business.controller;

import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.business.dto.PhotoNotifyRequest;
import org.enveloping.ecobin.business.dto.StsTokenRequest;
import org.enveloping.ecobin.business.dto.StsTokenResponse;
import org.enveloping.ecobin.business.service.PhotoNotifyService;
import org.enveloping.ecobin.common.result.Result;
import org.enveloping.ecobin.framework.cos.CosStsCredential;
import org.enveloping.ecobin.framework.cos.CosTokenClient;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 设备 IoT 照片上报接口（COS 直传模式）。
 * <p>
 * 鉴权采用明文 SN 信任（无用户登录态）：服务层按上报的 {@code sn} 反查设备确定租户。
 * 路径在 SecurityConfig 中以 {@code /api/iot/**} 放行。
 * <ul>
 *   <li>{@code sts}：设备请求 COS 临时密钥，用于直传照片到腾讯云 COS；</li>
 *   <li>{@code notify}：设备上传完成后回报照片 URL，后端回填到对应订单。</li>
 * </ul>
 */
@RestController
@RequestMapping("/api/iot/photo")
@RequiredArgsConstructor
public class IotPhotoController {

    private final CosTokenClient cosTokenClient;
    private final PhotoNotifyService photoNotifyService;

    /** 获取 COS STS 临时凭证 */
    @PostMapping("/sts")
    public Result<StsTokenResponse> sts(@Valid @RequestBody StsTokenRequest request) {
        CosStsCredential credential = cosTokenClient.getTempCredentials(request.getSn(), request.getDoorIndex());
        return Result.ok(StsTokenResponse.builder()
                .tmpSecretId(credential.getTmpSecretId())
                .tmpSecretKey(credential.getTmpSecretKey())
                .sessionToken(credential.getSessionToken())
                .startTime(credential.getStartTime())
                .expiredTime(credential.getExpiredTime())
                .bucket(credential.getBucket())
                .region(credential.getRegion())
                .baseUrl(credential.getBaseUrl())
                .uploadPrefix(credential.getUploadPrefix())
                .build());
    }

    /** 照片 URL 回报 */
    @PostMapping("/notify")
    public Result<Void> notify(@Valid @RequestBody PhotoNotifyRequest request) {
        photoNotifyService.notifyPhotos(request);
        return Result.ok();
    }
}
