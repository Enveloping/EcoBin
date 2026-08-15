package org.enveloping.ecobin.identity.application.platformminiapp;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.springframework.stereotype.Component;

import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.util.Base64;

@Component
class PlatformMiniappBindingTokenService {

    // 128 bits encoded as 22 Base64URL characters, fitting WeChat scene limits.
    private static final int TOKEN_BYTES = 16;

    private final SecureRandom secureRandom = new SecureRandom();

    IssuedToken issue() {
        byte[] bytes = new byte[TOKEN_BYTES];
        secureRandom.nextBytes(bytes);
        String value = Base64.getUrlEncoder()
                .withoutPadding()
                .encodeToString(bytes);
        return new IssuedToken(value, sha256(bytes));
    }

    byte[] digest(String token) {
        if (token == null || token.isBlank()) {
            throw invalid();
        }
        byte[] decoded;
        try {
            decoded = Base64.getUrlDecoder().decode(token);
        } catch (IllegalArgumentException malformed) {
            throw invalid();
        }
        if (decoded.length != TOKEN_BYTES
                || !Base64.getUrlEncoder()
                .withoutPadding()
                .encodeToString(decoded)
                .equals(token)) {
            throw invalid();
        }
        return sha256(decoded);
    }

    private static byte[] sha256(byte[] value) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(value);
        } catch (NoSuchAlgorithmException unavailable) {
            throw new IllegalStateException(
                    "SHA-256 is unavailable", unavailable);
        }
    }

    private static TargetApiException invalid() {
        return new TargetApiException(
                422,
                "IDENTITY.FACTORY_BINDING_TOKEN_INVALID",
                "厂家操作员绑定凭证无效");
    }

    record IssuedToken(String value, byte[] sha256) {

        IssuedToken {
            sha256 = sha256.clone();
        }

        @Override
        public byte[] sha256() {
            return sha256.clone();
        }
    }
}
