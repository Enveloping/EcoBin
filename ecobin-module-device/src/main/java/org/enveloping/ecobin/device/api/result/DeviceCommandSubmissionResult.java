package org.enveloping.ecobin.device.api.result;

import java.util.Objects;

/**
 * Redacted technical evidence from one transport attempt.
 */
public record DeviceCommandSubmissionResult(
        Outcome outcome,
        byte[] requestSha256,
        byte[] responseSha256,
        Integer httpStatus,
        String externalErrorCode,
        String externalRequestId,
        String redactedDiagnostic) {

    public DeviceCommandSubmissionResult {
        Objects.requireNonNull(outcome, "outcome");
        requestSha256 = copyDigest(requestSha256, "requestSha256");
        responseSha256 = copyDigest(responseSha256, "responseSha256");
        if (httpStatus != null
                && (httpStatus < 100 || httpStatus > 599)) {
            throw new IllegalArgumentException(
                    "httpStatus must be between 100 and 599");
        }
        if (externalErrorCode != null
                && !externalErrorCode.matches("[A-Za-z0-9._:-]{1,64}")) {
            throw new IllegalArgumentException(
                    "externalErrorCode has an invalid format");
        }
        if (externalRequestId != null
                && !externalRequestId.matches(
                        "[A-Za-z0-9._:-]{1,128}")) {
            throw new IllegalArgumentException(
                    "externalRequestId has an invalid format");
        }
        if (redactedDiagnostic != null
                && redactedDiagnostic.length() > 1000) {
            throw new IllegalArgumentException(
                    "redactedDiagnostic is too long");
        }
    }

    @Override
    public byte[] requestSha256() {
        return requestSha256 == null ? null : requestSha256.clone();
    }

    @Override
    public byte[] responseSha256() {
        return responseSha256 == null ? null : responseSha256.clone();
    }

    private static byte[] copyDigest(byte[] value, String name) {
        if (value == null) {
            return null;
        }
        if (value.length != 32) {
            throw new IllegalArgumentException(name + " must contain 32 bytes");
        }
        return value.clone();
    }

    public enum Outcome {
        PLATFORM_ACCEPTED,
        TARGET_OFFLINE,
        TARGET_NOT_FOUND,
        RETRYABLE_FAILURE,
        PERMANENT_FAILURE
    }
}
