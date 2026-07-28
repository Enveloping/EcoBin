package org.enveloping.ecobin.integration.fake;

import org.enveloping.ecobin.identity.api.error.WechatExchangeException;
import org.enveloping.ecobin.identity.api.port.WechatPhoneNumberPort;
import org.enveloping.ecobin.identity.api.port.WechatSessionPort;
import org.enveloping.ecobin.identity.api.result.WechatPhoneNumber;
import org.enveloping.ecobin.identity.api.result.WechatSession;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;

/**
 * 只接受显式 {@code fake:} code 的确定性微信会话替身。
 */
public final class FakeWechatSessionAdapter
        implements WechatSessionPort, WechatPhoneNumberPort {

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

    @Override
    public WechatSession exchangeByCredentialReference(
            String appid,
            String secretReference,
            String code) {
        return exchange(appid, "", code);
    }

    @Override
    public WechatPhoneNumber exchangePhoneNumberByCredentialReference(
            String appid,
            String secretReference,
            String phoneCode) {
        if (phoneCode == null || !phoneCode.startsWith("fake-phone:")) {
            throw new WechatExchangeException(
                    WechatExchangeException.Reason.INVALID_CODE,
                    "Fake WeChat phone adapter only accepts fake-phone: codes");
        }
        String phone = phoneCode.substring("fake-phone:".length());
        if (phone.isBlank()) {
            throw new WechatExchangeException(
                    WechatExchangeException.Reason.INVALID_CODE,
                    "Fake WeChat phone code does not contain a number");
        }
        return new WechatPhoneNumber(phone, null);
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
