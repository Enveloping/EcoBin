package org.enveloping.ecobin.operations.api.inbox;

import java.util.Objects;

/**
 * A permanently invalid message observed after transport authentication but
 * before a trustworthy business envelope could be admitted to the inbox.
 */
public record TrustedInboxRejection(
        String sourceNamespace,
        String sourcePrincipalKey,
        String externalMessageId,
        byte[] rawTransportBody,
        String reasonCode,
        String redactedDiagnostic) {

    public TrustedInboxRejection {
        Objects.requireNonNull(sourceNamespace, "sourceNamespace");
        Objects.requireNonNull(sourcePrincipalKey, "sourcePrincipalKey");
        Objects.requireNonNull(rawTransportBody, "rawTransportBody");
        Objects.requireNonNull(reasonCode, "reasonCode");
        if (rawTransportBody.length == 0) {
            throw new IllegalArgumentException(
                    "rawTransportBody must not be empty");
        }
        rawTransportBody = rawTransportBody.clone();
        if (!"PERMANENT_FORMAT_ERROR".equals(reasonCode)
                && !"UNSUPPORTED_SCHEMA".equals(reasonCode)
                && !"MISSING_STABLE_ID".equals(reasonCode)
                && !"UNRESOLVED_SCOPE".equals(reasonCode)) {
            throw new IllegalArgumentException(
                    "unsupported pre-inbox quarantine reason");
        }
        if (redactedDiagnostic != null
                && redactedDiagnostic.length() > 2000) {
            throw new IllegalArgumentException(
                    "redactedDiagnostic is too long");
        }
    }

    @Override
    public byte[] rawTransportBody() {
        return rawTransportBody.clone();
    }
}
