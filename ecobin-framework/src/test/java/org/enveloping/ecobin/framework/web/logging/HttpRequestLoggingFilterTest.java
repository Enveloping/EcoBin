package org.enveloping.ecobin.framework.web.logging;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.springframework.boot.test.system.CapturedOutput;
import org.springframework.boot.test.system.OutputCaptureExtension;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import tools.jackson.databind.json.JsonMapper;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.nio.charset.StandardCharsets;

import static org.assertj.core.api.Assertions.assertThat;

@ExtendWith(OutputCaptureExtension.class)
class HttpRequestLoggingFilterTest {

    @Test
    void logsArrivalAndCompletionWithoutLeakingPassword(
            CapturedOutput output) throws Exception {
        HttpRequestLoggingProperties properties =
                new HttpRequestLoggingProperties();
        properties.setEnabled(true);
        properties.setIncludeRequestBody(true);
        HttpRequestLoggingFilter filter =
                new HttpRequestLoggingFilter(
                        properties,
                        JsonMapper.builder().build());
        MockHttpServletRequest request =
                new MockHttpServletRequest(
                        "POST", "/api/v1/web/platform/auth/sessions");
        request.addHeader(
                "X-Request-ID", "request-log-test");
        request.addHeader(
                "Idempotency-Key",
                "00000000-0000-4000-8000-000000000001");
        request.setQueryString("source=developer");
        request.setContentType("application/json");
        request.setContent("""
                {"loginName":"admin","password":"admin123"}
                """.getBytes(StandardCharsets.UTF_8));
        MockHttpServletResponse response =
                new MockHttpServletResponse();

        filter.doFilter(
                request,
                response,
                (wrappedRequest, wrappedResponse) -> {
                    ((HttpServletRequest) wrappedRequest)
                            .getInputStream()
                            .readAllBytes();
                    ((HttpServletResponse) wrappedResponse)
                            .setStatus(201);
                });

        assertThat(output)
                .contains("HTTP_IN")
                .contains("HTTP_OUT")
                .contains("requestId=request-log-test")
                .contains("method=POST")
                .contains("status=201")
                .contains("\"loginName\":\"admin\"")
                .contains("\"password\":\"<redacted>\"")
                .doesNotContain("admin123");
        assertThat(response.getHeader("X-Request-Id"))
                .isEqualTo("request-log-test");
    }
}
