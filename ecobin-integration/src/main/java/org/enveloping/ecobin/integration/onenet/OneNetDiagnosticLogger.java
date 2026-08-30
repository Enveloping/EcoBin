package org.enveloping.ecobin.integration.onenet;

import org.enveloping.ecobin.framework.observability.DiagnosticLoggingProperties;
import org.enveloping.ecobin.framework.observability.DiagnosticPayloadSanitizer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.HexFormat;
import java.util.concurrent.TimeUnit;

/** OneNet 收发边界的统一、安全诊断日志。 */
@Component
public final class OneNetDiagnosticLogger {

    private static final Logger LOGGER = LoggerFactory.getLogger(
            "org.enveloping.ecobin.diagnostics.onenet");

    private final DiagnosticLoggingProperties properties;
    private final DiagnosticPayloadSanitizer sanitizer;

    public OneNetDiagnosticLogger(
            DiagnosticLoggingProperties properties,
            DiagnosticPayloadSanitizer sanitizer) {
        this.properties = properties;
        this.sanitizer = sanitizer;
    }

    public long started() {
        return System.nanoTime();
    }

    public void inboundMessage(
            String mqMessageId,
            int transportBytes,
            String decryptedJson) {
        if (!enabled()) {
            return;
        }
        LOGGER.info(
                "ONENET_IN direction=DEVICE_TO_BACKEND stage=DECRYPTED "
                        + "messageId={} transportBytes={} payload={}",
                text(mqMessageId),
                transportBytes,
                payload(decryptedJson));
    }

    public void inboundOutcome(
            String mqMessageId,
            String outcome,
            boolean acknowledged,
            long startedAt) {
        if (!enabled()) {
            return;
        }
        LOGGER.info(
                "ONENET_IN direction=DEVICE_TO_BACKEND stage=COMPLETED "
                        + "messageId={} outcome={} transportAck={} durationMs={}",
                text(mqMessageId),
                text(outcome),
                acknowledged ? "ACK" : "NEGATIVE_ACK",
                elapsedMs(startedAt));
    }

    public void inboundFailure(
            String mqMessageId,
            String stage,
            String outcome,
            boolean acknowledged,
            int transportBytes,
            String decryptedJson,
            Throwable failure,
            long startedAt) {
        if (!enabled()) {
            return;
        }
        String message =
                "ONENET_IN direction=DEVICE_TO_BACKEND stage={} "
                        + "messageId={} outcome={} transportAck={} "
                        + "transportBytes={} durationMs={} payload={}";
        Object[] arguments = new Object[]{
                text(stage),
                text(mqMessageId),
                text(outcome),
                acknowledged ? "ACK" : "NEGATIVE_ACK",
                transportBytes,
                elapsedMs(startedAt),
                payload(decryptedJson)
        };
        logFailure(message, arguments, failure);
    }

    public void outboundRequest(
            String taskUid,
            String hardwareSn,
            String commandType,
            String identifier,
            String endpoint,
            Object body,
            byte[] requestSha256) {
        if (!enabled()) {
            return;
        }
        LOGGER.info(
                "ONENET_OUT direction=BACKEND_TO_DEVICE stage=REQUEST "
                        + "taskUid={} hardwareSn={} commandType={} "
                        + "identifier={} endpoint={} requestSha256={} payload={}",
                text(taskUid),
                text(hardwareSn),
                text(commandType),
                text(identifier),
                text(endpoint),
                digest(requestSha256),
                payload(body));
    }

    public void outboundResponse(
            String taskUid,
            String hardwareSn,
            String commandType,
            int httpStatus,
            String externalCode,
            String responseBody,
            byte[] responseSha256,
            long startedAt) {
        if (!enabled()) {
            return;
        }
        LOGGER.info(
                "ONENET_OUT direction=BACKEND_TO_DEVICE stage=RESPONSE "
                        + "taskUid={} hardwareSn={} commandType={} httpStatus={} "
                        + "externalCode={} responseSha256={} durationMs={} payload={}",
                text(taskUid),
                text(hardwareSn),
                text(commandType),
                httpStatus,
                text(externalCode),
                digest(responseSha256),
                elapsedMs(startedAt),
                payload(responseBody));
    }

    public void outboundFailure(
            String taskUid,
            String hardwareSn,
            String commandType,
            String stage,
            Integer httpStatus,
            String externalCode,
            String responseBody,
            Throwable failure,
            long startedAt) {
        if (!enabled()) {
            return;
        }
        String message =
                "ONENET_OUT direction=BACKEND_TO_DEVICE stage={} "
                        + "taskUid={} hardwareSn={} commandType={} httpStatus={} "
                        + "externalCode={} durationMs={} response={}";
        Object[] arguments = new Object[]{
                text(stage),
                text(taskUid),
                text(hardwareSn),
                text(commandType),
                httpStatus == null ? "<none>" : httpStatus,
                text(externalCode),
                elapsedMs(startedAt),
                payload(responseBody)
        };
        logFailure(message, arguments, failure);
    }

    public Throwable sanitized(Throwable failure) {
        if (!enabled()
                || !properties.getOneNet().isIncludeStackTrace()) {
            return null;
        }
        return sanitizer.throwable(failure, 2_048);
    }

    /**
     * Sanitizes bounded text before it is persisted as technical evidence.
     * A missing message stays absent instead of becoming the log-only
     * {@code <none>} marker.
     */
    public String sanitizedText(String value, int maxLength) {
        if (value == null || value.isBlank()) {
            return null;
        }
        return sanitizer.text(value, maxLength);
    }

    private boolean enabled() {
        return properties.getOneNet().isEnabled();
    }

    private String payload(Object value) {
        if (!properties.getOneNet().isIncludePayload()) {
            return "<disabled>";
        }
        return sanitizer.json(
                value, properties.getOneNet().getMaxPayloadLength());
    }

    private String text(String value) {
        return sanitizer.text(value, 512);
    }

    private void logFailure(
            String message,
            Object[] arguments,
            Throwable failure) {
        if (failure != null
                && properties.getOneNet().isIncludeStackTrace()) {
            Object[] withFailure = new Object[arguments.length + 1];
            System.arraycopy(
                    arguments, 0, withFailure, 0, arguments.length);
            withFailure[arguments.length] = sanitized(failure);
            LOGGER.error(message, withFailure);
        } else {
            LOGGER.error(message, arguments);
        }
    }

    private static long elapsedMs(long startedAt) {
        return TimeUnit.NANOSECONDS.toMillis(
                System.nanoTime() - startedAt);
    }

    private static String digest(byte[] value) {
        return value == null
                ? "<none>"
                : HexFormat.of().formatHex(value);
    }
}
