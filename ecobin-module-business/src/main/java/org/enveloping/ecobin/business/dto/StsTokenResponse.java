package org.enveloping.ecobin.business.dto;

import lombok.Builder;
import lombok.Data;

/**
 * COS STS 临时凭证响应
 */
@Data
@Builder
public class StsTokenResponse {

    /** 临时 SecretId */
    private String tmpSecretId;

    /** 临时 SecretKey */
    private String tmpSecretKey;

    /** 会话令牌 */
    private String sessionToken;

    /** 凭证开始时间（Unix 秒） */
    private long startTime;

    /** 凭证过期时间（Unix 秒） */
    private long expiredTime;

    /** 存储桶名称 */
    private String bucket;

    /** 所属地域 */
    private String region;

    /** COS 访问域名 */
    private String baseUrl;

    /** 上传路径前缀（设备上传时的 Object Key 前缀） */
    private String uploadPrefix;
}
