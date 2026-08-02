package org.enveloping.ecobin.integration.onenet;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import org.enveloping.ecobin.framework.observability.DiagnosticLoggingProperties;
import org.enveloping.ecobin.framework.observability.DiagnosticPayloadSanitizer;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.json.JsonMapper;

import java.util.LinkedHashMap;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

class OneNetDiagnosticLoggerTest {

    @Test
    void detailedThrowableIsUnavailableWhenDiagnosticsAreDisabled() {
        DiagnosticLoggingProperties properties =
                new DiagnosticLoggingProperties();
        OneNetDiagnosticLogger diagnosticLogger =
                new OneNetDiagnosticLogger(
                        properties,
                        new DiagnosticPayloadSanitizer(
                                JsonMapper.builder().build()));

        assertThat(diagnosticLogger.sanitized(
                new IllegalStateException("secret=must-not-log")))
                .isNull();
    }

    @Test
    void detailedWireLogsNeverExposeCredentials() {
        DiagnosticLoggingProperties properties =
                new DiagnosticLoggingProperties();
        properties.getOneNet().setEnabled(true);
        OneNetDiagnosticLogger diagnosticLogger =
                new OneNetDiagnosticLogger(
                        properties,
                        new DiagnosticPayloadSanitizer(
                                JsonMapper.builder().build()));
        Logger logger = (Logger) LoggerFactory.getLogger(
                "org.enveloping.ecobin.diagnostics.onenet");
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        try {
            Map<String, Object> grant = new LinkedHashMap<>();
            grant.put("secretId", "TMP_SECRET_ID");
            grant.put("secretKey", "TMP_SECRET_KEY");
            grant.put("sessionToken", "TMP_SESSION_TOKEN");
            Map<String, Object> request = new LinkedHashMap<>();
            request.put("device_name", "test-device-1");
            request.put("cosGrant", grant);

            long startedAt = diagnosticLogger.started();
            diagnosticLogger.outboundRequest(
                    "task-1",
                    "test-device-1",
                    "START_DELIVERY_SESSION",
                    "startDeliverySession",
                    "https://iot.example/invoke",
                    request,
                    new byte[32]);
            diagnosticLogger.outboundFailure(
                    "task-1",
                    "test-device-1",
                    "START_DELIVERY_SESSION",
                    "HTTP_RESPONSE",
                    400,
                    "10415",
                    "{\"code\":10415,\"accessKey\":\"RESPONSE_KEY\"}",
                    new IllegalStateException(
                            "authorization=Bearer-visible"),
                    startedAt);

            String messages = appender.list.stream()
                    .map(ILoggingEvent::getFormattedMessage)
                    .reduce("", (left, right) -> left + "\n" + right);
            String throwableMessages = appender.list.stream()
                    .filter(event -> event.getThrowableProxy() != null)
                    .map(event -> event.getThrowableProxy().getMessage())
                    .reduce("", (left, right) -> left + "\n" + right);

            assertThat(messages)
                    .contains("test-device-1")
                    .contains("10415")
                    .contains("<redacted>")
                    .doesNotContain("TMP_SECRET_ID")
                    .doesNotContain("TMP_SECRET_KEY")
                    .doesNotContain("TMP_SESSION_TOKEN")
                    .doesNotContain("RESPONSE_KEY");
            assertThat(throwableMessages)
                    .doesNotContain("Bearer-visible")
                    .contains("<redacted>");
        } finally {
            logger.detachAppender(appender);
            appender.stop();
        }
    }
}
