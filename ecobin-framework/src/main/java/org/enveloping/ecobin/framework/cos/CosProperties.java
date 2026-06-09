package org.enveloping.ecobin.framework.cos;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/**
 * 腾讯云 COS 对象存储配置（STS 临时密钥模式，设备直传）。
 * <p>
 * 当前字段默认留空；{@link CosStsClient} 在凭证缺失时仅记录占位日志、返回占位凭证。
 */
@Data
@Component
@ConfigurationProperties(prefix = "cos")
public class CosProperties {

    /** 腾讯云 SecretId */
    private String secretId;

    /** 腾讯云 SecretKey */
    private String secretKey;

    /** 存储桶所属地域（如 ap-guangzhou） */
    private String region;

    /** 存储桶名称（格式 bucket-appId） */
    private String bucketName;

    /** COS 访问域名（如 https://bucket-appId.cos.ap-guangzhou.myqcloud.com） */
    private String baseUrl;

    /** 临时密钥有效期（秒），默认 1800（30 分钟） */
    private int durationSeconds = 1800;

    /** 配置是否齐全（齐全时才会请求真实 STS） */
    public boolean isConfigured() {
        return secretId != null && !secretId.isBlank()
                && secretKey != null && !secretKey.isBlank()
                && region != null && !region.isBlank()
                && bucketName != null && !bucketName.isBlank();
    }
}
