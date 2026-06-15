package org.enveloping.ecobin.framework.cos;

import com.tencent.cloud.Policy;
import com.tencent.cloud.Response;
import com.tencent.cloud.Statement;
import com.tencent.cloud.cos.util.Jackson;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.util.TreeMap;

/**
 * 腾讯云 COS STS 临时密钥客户端（设备直传模式）。
 * <p>
 * 凭证未到位前为占位实现：仅记录指令意图、返回占位凭证（设备端可用其联调，但无法真实上传）。
 * 待 {@link CosProperties} 配置齐全后，{@link #getRealTempCredentials} 会调用
 * {@code com.tencent.cloud.CosStsClient.getCredential} 获取真实临时凭证。
 * <p>
 * 使用方式：照片 key 由后端开门时确定性生成（{@link #buildPhotoKeys}），随开门命令把凭证 + key
 * 一并下发给设备，设备按 key 直传 COS；后端开门即把对应 URL 预存进订单，无需设备回传。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class CosTokenClient {

    private final CosProperties properties;

    /** 占位 baseUrl（凭证未配置时用于联调） */
    private static final String PLACEHOLDER_BASE_URL = "https://placeholder.cos.ap-guangzhou.myqcloud.com";

    /**
     * 获取设备直传 COS 所需的 STS 临时凭证。
     *
     * @param deviceSn  设备序列号（仅用于日志）
     * @param doorIndex 投口号（仅用于日志）
     * @return 临时凭证三件套 + bucket/region/baseUrl
     */
    public CosStsCredential getTempCredentials(String deviceSn, Integer doorIndex) {
        if (!properties.isConfigured()) {
            log.info("[COS·占位] 请求临时凭证 deviceSn={}, doorIndex={}（凭证未配置，返回占位值）",
                    deviceSn, doorIndex);
            return CosStsCredential.builder()
                    .tmpSecretId("PLACEHOLDER_TMP_SECRET_ID")
                    .tmpSecretKey("PLACEHOLDER_TMP_SECRET_KEY")
                    .sessionToken("PLACEHOLDER_SESSION_TOKEN")
                    .startTime(Instant.now().getEpochSecond())
                    .expiredTime(Instant.now().getEpochSecond() + properties.getDurationSeconds())
                    .bucket(nullToDefault(properties.getBucketName(), "placeholder-bucket-1234567890"))
                    .region(nullToDefault(properties.getRegion(), "ap-guangzhou"))
                    .baseUrl(nullToDefault(properties.getBaseUrl(), PLACEHOLDER_BASE_URL))
                    .build();
        }

        return getRealTempCredentials();
    }

    /**
     * 生成一笔订单 4 张照片的 COS 对象 key（确定性、与订单 token 绑定，避免同投口多单互相覆盖）。
     *
     * @param deviceSn  设备序列号
     * @param doorIndex 投口号
     * @param token     订单关联键：投递 deliveryToken / 清运 cleanOrderId
     */
    public CosPhotoKeys buildPhotoKeys(String deviceSn, Integer doorIndex, String token) {
        String prefix = deviceSn + "/" + doorIndex + "/" + token + "/";
        return new CosPhotoKeys(
                prefix + "open_outside.jpg",
                prefix + "open_inside.jpg",
                prefix + "close_outside.jpg",
                prefix + "close_inside.jpg");
    }

    /** 由对象 key 拼出可访问的完整 URL（{@code baseUrl + "/" + key}）。 */
    public String toUrl(String key) {
        return nullToDefault(properties.getBaseUrl(), PLACEHOLDER_BASE_URL) + "/" + key;
    }

    /**
     * 真实 STS 调用（凭证齐全时）。
     */
    private CosStsCredential getRealTempCredentials() {
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

            return CosStsCredential.builder()
                    .tmpSecretId(response.credentials.tmpSecretId)
                    .tmpSecretKey(response.credentials.tmpSecretKey)
                    .sessionToken(response.credentials.sessionToken)
                    .startTime(response.startTime)
                    .expiredTime(response.expiredTime)
                    .bucket(bucketName)
                    .region(properties.getRegion())
                    .baseUrl(properties.getBaseUrl())
                    .build();
        } catch (Exception e) {
            log.error("[COS] 获取临时凭证失败", e);
            throw new RuntimeException("获取 COS 临时凭证失败: " + e.getMessage(), e);
        }
    }

    private static String nullToDefault(String value, String defaultValue) {
        return value != null && !value.isBlank() ? value : defaultValue;
    }
}
