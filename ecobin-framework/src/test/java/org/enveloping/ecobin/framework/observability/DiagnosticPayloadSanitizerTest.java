package org.enveloping.ecobin.framework.observability;

import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;

import static org.assertj.core.api.Assertions.assertThat;

class DiagnosticPayloadSanitizerTest {

    private final DiagnosticPayloadSanitizer sanitizer =
            new DiagnosticPayloadSanitizer(
                    JsonMapper.builder().build());

    @Test
    void redactsEveryExternalCredentialButKeepsBusinessIdentity() {
        String sanitized = sanitizer.json("""
                {
                  "hardwareSn": "test-device-1",
                  "deviceCode": "Dv_0123456789abcdefghijklmn",
                  "accessKey": "ONENET_ACCESS_KEY",
                  "authorization": "version=2022&res=x&sign=secret",
                  "cosGrant": {
                    "secretId": "TMP_SECRET_ID",
                    "secretKey": "TMP_SECRET_KEY",
                    "sessionToken": "TMP_SESSION_TOKEN"
                  },
                  "wechatPhoneCode": "PHONE_CODE",
                  "photoUrl": "https://cos.example/a.jpg?q-signature=VISIBLE"
                }
                """, 32_768);

        assertThat(sanitized)
                .contains("\"hardwareSn\":\"test-device-1\"")
                .contains("\"deviceCode\":\"Dv_0123456789abcdefghijklmn\"")
                .contains("\"accessKey\":\"<redacted>\"")
                .contains("\"secretId\":\"<redacted>\"")
                .contains("\"sessionToken\":\"<redacted>\"")
                .doesNotContain("ONENET_ACCESS_KEY")
                .doesNotContain("TMP_SECRET_ID")
                .doesNotContain("TMP_SECRET_KEY")
                .doesNotContain("TMP_SESSION_TOKEN")
                .doesNotContain("PHONE_CODE")
                .doesNotContain("VISIBLE");
    }

    @Test
    void sanitizedThrowableKeepsClassAndStackButRemovesSecrets() {
        IllegalStateException original = new IllegalStateException(
                "authorization: Bearer bearer-secret "
                        + "cookie: JSESSIONID=cookie-secret, "
                        + "body={\"access_token\":\"json-secret\"} "
                        + "password=admin123 "
                        + "url=https://example.test/a?token=visible");
        original.setStackTrace(new StackTraceElement[]{
                new StackTraceElement(
                        "Example", "call", "Example.java", 42)
        });

        Throwable sanitized = sanitizer.throwable(original, 2_048);

        assertThat(sanitized.getMessage())
                .contains(IllegalStateException.class.getName())
                .contains("<redacted>")
                .doesNotContain("bearer-secret")
                .doesNotContain("cookie-secret")
                .doesNotContain("json-secret")
                .doesNotContain("admin123")
                .doesNotContain("visible");
        assertThat(sanitized.getStackTrace())
                .containsExactly(original.getStackTrace());
    }

    @Test
    void malformedJsonIsNeverEchoedBack() {
        assertThat(sanitizer.json(
                "{\"secretKey\":\"must-not-leak", 32_768))
                .contains("malformed JSON")
                .doesNotContain("must-not-leak");
    }
}
