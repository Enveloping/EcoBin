package org.enveloping.ecobin.integration.wechatpay;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Data
@Component
@ConfigurationProperties(prefix = "ecobin.funds.wechat-pay")
public class WechatPayProperties {

    private String baseUrl = "https://api.mch.weixin.qq.com";
    private String mchid;
    private String merchantSerialNumber;
    private String merchantPrivateKeyPath;
    private String apiV3Key;
    private String platformCertificatePath;
    private String transferSceneId = "1010";
    private int connectTimeoutMillis = 5000;
    private int requestTimeoutMillis = 10000;

    public boolean isConfigured() {
        return text(baseUrl) && text(mchid) && text(merchantSerialNumber)
                && text(merchantPrivateKeyPath) && text(apiV3Key)
                && text(platformCertificatePath) && text(transferSceneId)
                && apiV3Key.getBytes(java.nio.charset.StandardCharsets.UTF_8).length == 32
                && connectTimeoutMillis > 0 && requestTimeoutMillis > 0;
    }

    private static boolean text(String value) {
        return value != null && !value.isBlank();
    }
}
