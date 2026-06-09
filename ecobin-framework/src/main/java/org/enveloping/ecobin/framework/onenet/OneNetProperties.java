package org.enveloping.ecobin.framework.onenet;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/**
 * 中国移动 OneNet 物联网平台配置（设备命令下行）。
 * <p>
 * 当前凭证未到位，{@code baseUrl/productId/accessKey} 默认留空；{@link OneNetClient} 在凭证缺失时仅记录占位日志、不发起真实请求。
 */
@Data
@Component
@ConfigurationProperties(prefix = "onenet")
public class OneNetProperties {

    /** OneNet API 基地址 */
    private String baseUrl;

    /** 产品ID */
    private String productId;

    /** 访问密钥 / API Token */
    private String accessKey;

    /** 配置是否齐全（齐全才会发起真实下发） */
    public boolean isConfigured() {
        return baseUrl != null && !baseUrl.isBlank()
                && productId != null && !productId.isBlank()
                && accessKey != null && !accessKey.isBlank();
    }
}
