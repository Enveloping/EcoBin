package org.enveloping.ecobin.integration.wechatpay;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class WechatPayPropertiesTest {

    @Test
    void realConfigurationRejectsTheDefaultPlaceholderNotificationHost() {
        WechatPayProperties properties = completeProperties();

        assertFalse(properties.isConfigured());
    }

    @Test
    void realConfigurationAcceptsOnlyAnHttpsOriginWithoutQueryOrPath() {
        WechatPayProperties properties = completeProperties();
        properties.setNotifyBaseUrl("https://pay-notify.ecobin.cn");
        assertTrue(properties.isConfigured());

        properties.setNotifyBaseUrl("http://pay-notify.ecobin.cn");
        assertFalse(properties.isConfigured());
        properties.setNotifyBaseUrl("https://pay-notify.ecobin.cn/root");
        assertFalse(properties.isConfigured());
        properties.setNotifyBaseUrl(
                "https://pay-notify.ecobin.cn?token=forbidden");
        assertFalse(properties.isConfigured());
        properties.setNotifyBaseUrl("https://127.0.0.1");
        assertFalse(properties.isConfigured());
    }

    @Test
    void realConfigurationRequiresAWechatPayPublicKeyId() {
        WechatPayProperties properties = completeProperties();
        properties.setNotifyBaseUrl("https://pay-notify.ecobin.cn");

        properties.setPublicKeyId("certificate-serial");
        assertFalse(properties.isConfigured());
        properties.setPublicKeyId(
                "PUB_KEY_ID_0116571234562024052000123400000000");
        assertTrue(properties.isConfigured());
    }

    private static WechatPayProperties completeProperties() {
        WechatPayProperties properties = new WechatPayProperties();
        properties.setMchid("1900000109");
        properties.setMerchantSerialNumber("SERIAL");
        properties.setMerchantPrivateKeyPath("/run/secrets/apiclient_key.pem");
        properties.setApiV3Key("0123456789abcdef0123456789abcdef");
        properties.setPublicKeyId(
                "PUB_KEY_ID_0116571234562024052000123400000000");
        properties.setPublicKeyPath("/run/secrets/pub_key.pem");
        return properties;
    }
}
