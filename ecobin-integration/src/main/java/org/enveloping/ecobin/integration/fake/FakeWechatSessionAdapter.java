package org.enveloping.ecobin.integration.fake;

import org.enveloping.ecobin.identity.api.port.WechatSessionPort;
import org.enveloping.ecobin.identity.api.result.WechatSession;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;

/**
 * 只接受显式 {@code fake:} code 的确定性微信会话替身。
 */
public final class FakeWechatSessionAdapter implements WechatSessionPort {

    @Override
    public WechatSession exchange(String appid, String secret, String code) {
        if (code == null || !code.startsWith("fake:")) {
            throw new IllegalArgumentException(
                    "Fake WeChat adapter only accepts codes prefixed with fake:");
        }
        return new WechatSession(
                "fake_openid_" + digest(code).substring(0, 32),
                null);
    }

    private static String digest(String value) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(
                    digest.digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
    }
}
