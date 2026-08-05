package org.enveloping.ecobin.integration.wechatpay;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

import java.net.URI;
import java.util.Locale;

@Data
@Component
@ConfigurationProperties(prefix = "ecobin.funds.wechat-pay")
public class WechatPayProperties {

    private String baseUrl = "https://api.mch.weixin.qq.com";
    private String mchid;
    private String merchantSerialNumber;
    private String merchantPrivateKeyPath;
    private String apiV3Key;
    private String publicKeyId;
    private String publicKeyPath;
    private String notifyBaseUrl = "https://fake.invalid";
    private String transferSceneId = "1010";
    private int connectTimeoutMillis = 5000;
    private int requestTimeoutMillis = 10000;

    public boolean isConfigured() {
        return text(baseUrl) && text(mchid) && text(merchantSerialNumber)
                && text(merchantPrivateKeyPath) && text(apiV3Key)
                && validPublicKeyId(publicKeyId) && text(publicKeyPath)
                && text(transferSceneId)
                && validPublicNotifyBaseUrl(notifyBaseUrl)
                && apiV3Key.getBytes(java.nio.charset.StandardCharsets.UTF_8).length == 32
                && connectTimeoutMillis > 0 && requestTimeoutMillis > 0;
    }

    static boolean validPublicNotifyBaseUrl(String value) {
        if (!text(value)) return false;
        try {
            URI uri = URI.create(value.trim());
            String host = uri.getHost();
            String path = uri.getRawPath();
            if (!"https".equalsIgnoreCase(uri.getScheme())
                    || !text(host)
                    || uri.getRawUserInfo() != null
                    || uri.getRawQuery() != null
                    || uri.getRawFragment() != null
                    || (text(path) && !"/".equals(path))) {
                return false;
            }
            String normalizedHost = host.toLowerCase(Locale.ROOT);
            if (normalizedHost.equals("localhost")
                    || normalizedHost.endsWith(".localhost")
                    || normalizedHost.endsWith(".local")
                    || normalizedHost.endsWith(".invalid")
                    || normalizedHost.endsWith(".test")
                    || normalizedHost.equals("example.com")
                    || normalizedHost.equals("example.org")
                    || normalizedHost.equals("example.net")) {
                return false;
            }
            return !privateOrReservedAddress(normalizedHost);
        } catch (IllegalArgumentException invalid) {
            return false;
        }
    }

    static boolean validPublicKeyId(String value) {
        return value != null
                && value.equals(value.trim())
                && value.matches("^PUB_KEY_ID_[0-9A-Za-z]+$");
    }

    private static boolean privateOrReservedAddress(String host) {
        if (host.contains(":")) {
            String value = host.replace("[", "").replace("]", "");
            return value.equals("::1") || value.equals("::")
                    || value.startsWith("fc") || value.startsWith("fd")
                    || value.startsWith("fe8") || value.startsWith("fe9")
                    || value.startsWith("fea") || value.startsWith("feb");
        }
        if (!host.matches("[0-9]{1,3}(\\.[0-9]{1,3}){3}")) return false;
        String[] parts = host.split("\\.");
        int[] octets = new int[4];
        for (int index = 0; index < parts.length; index++) {
            octets[index] = Integer.parseInt(parts[index]);
            if (octets[index] > 255) return true;
        }
        int first = octets[0];
        int second = octets[1];
        return first == 0 || first == 10 || first == 127 || first >= 224
                || (first == 100 && second >= 64 && second <= 127)
                || (first == 169 && second == 254)
                || (first == 172 && second >= 16 && second <= 31)
                || (first == 192 && second == 168)
                || (first == 192 && second == 0 && octets[2] == 2)
                || (first == 198 && (second == 18 || second == 19
                || second == 51 && octets[2] == 100))
                || (first == 203 && second == 0 && octets[2] == 113);
    }

    private static boolean text(String value) {
        return value != null && !value.isBlank();
    }
}
