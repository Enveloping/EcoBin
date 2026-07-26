package org.enveloping.ecobin.integration.cos;

import com.tencent.cloud.Policy;
import com.tencent.cloud.Response;
import com.tencent.cloud.Statement;
import com.tencent.cloud.cos.util.Jackson;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.enveloping.ecobin.device.api.port.CosUploadCredentialPort;
import org.enveloping.ecobin.device.api.result.CosUploadCredential;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

import java.util.TreeMap;

/**
 * 腾讯云 COS STS 临时密钥客户端（设备直传模式）。
 * <p>
 * 仅在 {@code ecobin.external.mode=real} 时装配，并要求 {@link CosProperties}
 * 配置完整；Fake 模式使用只返回 {@code .invalid} 域名的无网络替身。
 * 真实实现通过 {@link #getRealTempCredentials} 调用
 * {@code com.tencent.cloud.CosStsClient.getCredential} 获取真实临时凭证。
 * <p>
 * 使用方式：开门命令只下发<strong>凭证</strong>（不含照片 key）；照片对象 key 由<strong>设备自定</strong>
 * （投递、清运一致），设备直传 COS 后把 URL 随上行事件回传，后端原样存。
 */
@Slf4j
@Component
@ConditionalOnProperty(
        prefix = "ecobin.external",
        name = "mode",
        havingValue = "real")
@RequiredArgsConstructor
public class CosTokenClient implements CosUploadCredentialPort {

    private final CosProperties properties;

    /**
     * 获取设备直传 COS 所需的 STS 临时凭证。
     *
     * @param deviceSn  设备序列号（仅用于日志）
     * @param doorIndex 投口号（仅用于日志）
     * @return 临时凭证三件套 + bucket/region/baseUrl
     */
    @Override
    public CosUploadCredential issue(String deviceSn, Integer doorIndex) {
        if (!properties.isConfigured()) {
            throw new IllegalStateException(
                    "REAL mode requires complete COS configuration");
        }

        return getRealTempCredentials();
    }

    /**
     * 真实 STS 调用（凭证齐全时）。
     */
    private CosUploadCredential getRealTempCredentials() {
        TreeMap<String, Object> config = new TreeMap<>();
        config.put("secretId", properties.getSecretId());
        config.put("secretKey", properties.getSecretKey());
        config.put("durationSeconds", properties.getDurationSeconds());
        config.put("bucket", properties.getBucketName());
        config.put("region", properties.getRegion());

        String bucketName = properties.getBucketName();
        String appId = bucketName.contains("-") ? bucketName.substring(bucketName.lastIndexOf("-") + 1) : "";

        Policy policy = new Policy();
        Statement statement = new Statement();
        statement.setEffect("allow");
        statement.addActions(new String[]{
                "name/cos:PutObject",
                "name/cos:PostObject",
                "cos:InitiateMultipartUpload",
                "cos:ListMultipartUploads",
                "cos:ListParts",
                "cos:UploadPart",
                "cos:CompleteMultipartUpload",
        });
        statement.addResources(new String[]{
                String.format("qcs::cos:%s:uid/%s:%s/*", properties.getRegion(), appId, bucketName),
                String.format("qcs::ci:%s:uid/%s:bucket/%s/*", properties.getRegion(), appId, bucketName)
        });

        policy.addStatement(statement);
        config.put("policy", Jackson.toJsonPrettyString(policy));

        try {
            Response response = com.tencent.cloud.CosStsClient.getCredential(config);
            log.info("[COS] 临时凭证下发成功 expiredTime={}", response.expiredTime);

            return new CosUploadCredential(
                    response.credentials.tmpSecretId,
                    response.credentials.tmpSecretKey,
                    response.credentials.sessionToken,
                    response.startTime,
                    response.expiredTime,
                    bucketName,
                    properties.getRegion(),
                    properties.getBaseUrl());
        } catch (Exception e) {
            log.error("[COS] 获取临时凭证失败", e);
            throw new RuntimeException("获取 COS 临时凭证失败: " + e.getMessage(), e);
        }
    }
}
