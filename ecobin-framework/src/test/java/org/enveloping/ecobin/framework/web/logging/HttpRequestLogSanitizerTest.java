package org.enveloping.ecobin.framework.web.logging;

import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;

import java.nio.charset.StandardCharsets;

import static org.assertj.core.api.Assertions.assertThat;

class HttpRequestLogSanitizerTest {

    private final HttpRequestLogSanitizer sanitizer =
            new HttpRequestLogSanitizer(
                    JsonMapper.builder().build());

    @Test
    void redactsCredentialsAndPersonalDataButKeepsBusinessFields() {
        String body = sanitizer.body("""
                        {
                          "loginName": "admin",
                          "password": "admin123",
                          "nested": {
                            "wxLoginCode": "one-time-code",
                            "contactPhone": "13900000000",
                            "organizationCode": "org-a"
                          }
                        }
                        """.getBytes(StandardCharsets.UTF_8),
                "application/json;charset=UTF-8",
                false);

        assertThat(body)
                .contains("\"loginName\":\"admin\"")
                .contains("\"organizationCode\":\"org-a\"")
                .contains("\"password\":\"<redacted>\"")
                .contains("\"wxLoginCode\":\"<redacted>\"")
                .contains("\"contactPhone\":\"<redacted>\"")
                .doesNotContain("admin123")
                .doesNotContain("one-time-code")
                .doesNotContain("13900000000");
    }

    @Test
    void redactsSensitiveQueryValuesAndRemovesControlCharacters() {
        String query = sanitizer.query(
                "tenantCode=tenant-a&wechatPhoneCode=secret-value"
                        + "&search=line%0Abreak");

        assertThat(query)
                .contains("tenantCode=[tenant-a]")
                .contains("wechatPhoneCode=[<redacted>]")
                .contains("search=[line?break]")
                .doesNotContain("secret-value")
                .doesNotContain("\n");
    }

    @Test
    void neverLogsPartialOrMalformedJsonBody() {
        assertThat(sanitizer.body(
                "{\"password\":\"visible".getBytes(StandardCharsets.UTF_8),
                "application/json",
                false))
                .doesNotContain("visible")
                .contains("malformed JSON");
        assertThat(sanitizer.body(
                "{\"safe\":\"value\"}".getBytes(StandardCharsets.UTF_8),
                "application/json",
                true))
                .isEqualTo("<body truncated before logging>");
    }
}
