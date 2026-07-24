package org.enveloping.ecobin.device.api.result;

/**
 * 设备直传 COS 所需的规范化临时凭证。
 */
public record CosUploadCredential(
        String tmpSecretId,
        String tmpSecretKey,
        String sessionToken,
        long startTime,
        long expiredTime,
        String bucket,
        String region,
        String baseUrl) {
}
