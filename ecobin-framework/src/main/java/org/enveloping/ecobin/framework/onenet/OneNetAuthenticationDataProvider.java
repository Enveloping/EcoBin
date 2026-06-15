package org.enveloping.ecobin.framework.onenet;

import org.apache.pulsar.client.api.AuthenticationDataProvider;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;

/**
 * OneNet 北向 MQ 的 Pulsar 鉴权数据提供者（移植官方 SDK demo）。
 * <p>
 * token = {@code {"tenant":<accessId>,"password":<sign>}}，其中
 * {@code sign = sha256Hex(accessId + sha256Hex(secretKey)).substring(4, 20)}。
 */
public class OneNetAuthenticationDataProvider implements AuthenticationDataProvider {

    private final String token;

    public OneNetAuthenticationDataProvider(String accessId, String secretKey) {
        this.token = String.format("{\"tenant\":\"%s\",\"password\":\"%s\"}",
                accessId, sha256Hex(accessId + sha256Hex(secretKey)).substring(4, 20));
    }

    private static String sha256Hex(String input) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            byte[] digest = md.digest(input.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (Exception e) {
            throw new IllegalStateException("SHA-256 计算失败", e);
        }
    }

    @Override
    public boolean hasDataForHttp() {
        return false;
    }

    @Override
    public boolean hasDataFromCommand() {
        return true;
    }

    @Override
    public String getCommandData() {
        return token;
    }
}
