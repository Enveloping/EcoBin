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
 * 仅在 {@code ecobin.external.mode=real} 时装配。照片写入要求
 * {@link CosProperties} 配置完整；MCU 固件读取使用与业务程序发布包相同的私有更新包桶。
 * Fake 模式使用只返回 {@code .invalid} 域名的无网络替身。
 * 真实实现通过 {@link #getRealTempCredentials} 调用
 * {@code com.tencent.cloud.CosStsClient.getCredential} 获取真实临时凭证。
 * <p>
 * 使用方式：开始命令只下发<strong>短期凭证和作业目录前缀</strong>，
 * 不为四张照片推导具体对象 key；设备在该前缀内按契约命名并直传，
 * 完成事件再回传 URL。
 */
@Slf4j
@Component
@ConditionalOnProperty(
        prefix = "ecobin.external",
        name = "mode",
        havingValue = "real")
@RequiredArgsConstructor
public class CosTokenClient implements CosUploadCredentialPort {

    private final CosProperties photoProperties;
    private final BusinessReleaseArtifactProperties updatePackageProperties;

    /**
     * 获取设备直传 COS 所需的 STS 临时凭证。
     *
     * @param deviceSn  设备序列号（仅用于日志）
     * @param doorIndex 投口号（仅用于日志）
     * @param keyPrefix 本次投递或清运独占的对象目录前缀
     * @return 临时凭证三件套 + bucket/region/baseUrl
     */
    @Override
    public CosUploadCredential issue(
            String deviceSn,
            Integer doorIndex,
            String keyPrefix) {
        boolean firmwareRead = keyPrefix != null && keyPrefix.matches(
                "^ecobin/mcu-firmware/"
                        + "[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}"
                        + "-[89ab][0-9a-f]{3}-[0-9a-f]{12}/$");
        boolean workUpload = keyPrefix != null && keyPrefix.matches(
                "^ecobin/(delivery-session|clean-operation|device-acceptance)/"
                        + "[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}"
                        + "-[89ab][0-9a-f]{3}-[0-9a-f]{12}/$");
        if (!firmwareRead && !workUpload) {
            throw new IllegalArgumentException(
                    "COS work prefix is outside the target contract");
        }

        return getRealTempCredentials(
                keyPrefix,
                firmwareRead,
                grantTarget(firmwareRead));
    }

    /**
     * 真实 STS 调用（凭证齐全时）。
     */
    private CosUploadCredential getRealTempCredentials(
            String keyPrefix,
            boolean firmwareRead,
            GrantTarget target) {
        TreeMap<String, Object> config = new TreeMap<>();
        config.put("secretId", target.secretId());
        config.put("secretKey", target.secretKey());
        config.put("durationSeconds", target.durationSeconds());
        config.put("bucket", target.bucketName());
        config.put("region", target.region());

        String bucketName = target.bucketName();
        String appId = bucketName.contains("-") ? bucketName.substring(bucketName.lastIndexOf("-") + 1) : "";

        Policy policy = new Policy();
        Statement statement = new Statement();
        statement.setEffect("allow");
        java.util.ArrayList<String> actions = firmwareRead
                ? new java.util.ArrayList<>(java.util.List.of(
                        "name/cos:GetObject"))
                : new java.util.ArrayList<>(java.util.List.of(
                        "name/cos:PutObject",
                        "name/cos:PostObject",
                        "cos:InitiateMultipartUpload",
                        "cos:ListMultipartUploads",
                        "cos:ListParts",
                        "cos:UploadPart",
                        "cos:CompleteMultipartUpload"));
        if (!firmwareRead
                && keyPrefix.startsWith("ecobin/device-acceptance/")) {
            // Machine acceptance proves that COS can return the exact bytes
            // just uploaded. Business-photo grants remain upload-only.
            actions.add("name/cos:GetObject");
        }
        statement.addActions(actions.toArray(String[]::new));
        statement.addResources(new String[]{
                String.format(
                        "qcs::cos:%s:uid/%s:%s/%s*",
                        target.region(),
                        appId,
                        bucketName,
                        keyPrefix),
                String.format(
                        "qcs::ci:%s:uid/%s:bucket/%s/%s*",
                        target.region(),
                        appId,
                        bucketName,
                        keyPrefix)
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
                    target.region(),
                    target.baseUrl());
        } catch (Exception e) {
            log.error("[COS] 获取临时凭证失败", e);
            throw new RuntimeException("获取 COS 临时凭证失败: " + e.getMessage(), e);
        }
    }

    GrantTarget grantTarget(boolean firmwareRead) {
        if (!firmwareRead) {
            if (!photoProperties.isConfigured()) {
                throw new IllegalStateException(
                        "真实模式缺少完整的照片 COS 配置");
            }
            return new GrantTarget(
                    photoProperties.getSecretId(),
                    photoProperties.getSecretKey(),
                    photoProperties.getRegion(),
                    photoProperties.getBucketName(),
                    photoProperties.getBaseUrl(),
                    photoProperties.getDurationSeconds());
        }
        if (!updatePackageProperties.isDownloadLocationConfigured()) {
            throw new IllegalStateException(
                    "真实模式缺少完整的私有更新包 COS 配置");
        }
        return new GrantTarget(
                updatePackageProperties.getSecretId(),
                updatePackageProperties.getSecretKey(),
                updatePackageProperties.getRegion(),
                updatePackageProperties.getBucketName(),
                updatePackageProperties.getDownloadBaseUrl(),
                photoProperties.getDurationSeconds());
    }

    record GrantTarget(
            String secretId,
            String secretKey,
            String region,
            String bucketName,
            String baseUrl,
            int durationSeconds) {
    }
}
