package org.enveloping.ecobin.framework.cos;

import lombok.Builder;
import lombok.Data;

/**
 * COS 临时密钥凭证，由 {@link CosTokenClient#getTempCredentials} 返回。
 * <p>
 * 设备侧用临时密钥直传 COS：
 * <ul>
 *   <li>{@code tmpSecretId / tmpSecretKey / sessionToken} — 临时凭证三要素</li>
 *   <li>{@code bucket / region / baseUrl} — 上传目标</li>
 * </ul>
 * 上传对象的 key 不在凭证里：由<strong>设备自定</strong>（投递、清运一致），设备直传后把 URL 随上行事件回传后端。
 */
@Data
@Builder
public class CosStsCredential {

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
}
