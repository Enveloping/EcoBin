package org.enveloping.ecobin.operations.api.inbox;

import org.enveloping.ecobin.framework.reliability.TrustedInboxScopeResolver;

import java.util.Arrays;
import java.util.Objects;
import java.util.UUID;

/**
 * 外部适配器完成认证、解密、白名单规范化后的可信入站消息。
 *
 * <p>作用域由业务权威 resolver 在收件事务内解析；外部自报的租户或机构字段永远不作为
 * 数据库作用域来源。保留无 resolver 的构造器只用于平台级 Fake/维护消息。</p>
 */
public final class TrustedInboxMessage {

    private static final int MAX_RAW_BYTES = 1_048_576;
    private static final int MAX_NORMALIZED_CHARS = 262_144;

    private final String sourceNamespace;
    private final String sourcePrincipalKey;
    private final String externalMessageId;
    private final String messageKind;
    private final int normalizedSchemaVersion;
    private final byte[] rawTransportBody;
    private final String normalizedPayload;
    private final String authenticationMethod;
    private final String authenticationPrincipalRef;
    private final UUID correlationUid;
    private final UUID causationUid;
    private final TrustedInboxExecutionLane executionLane;
    private final TrustedInboxScopeResolver scopeResolver;

    public TrustedInboxMessage(
            String sourceNamespace,
            String sourcePrincipalKey,
            String externalMessageId,
            String messageKind,
            int normalizedSchemaVersion,
            byte[] rawTransportBody,
            String normalizedPayload,
            String authenticationMethod,
            String authenticationPrincipalRef,
            UUID correlationUid,
            UUID causationUid,
            TrustedInboxExecutionLane executionLane) {
        this(
                sourceNamespace,
                sourcePrincipalKey,
                externalMessageId,
                messageKind,
                normalizedSchemaVersion,
                rawTransportBody,
                normalizedPayload,
                authenticationMethod,
                authenticationPrincipalRef,
                correlationUid,
                causationUid,
                executionLane,
                TrustedInboxScopeResolver.platform());
    }

    public TrustedInboxMessage(
            String sourceNamespace,
            String sourcePrincipalKey,
            String externalMessageId,
            String messageKind,
            int normalizedSchemaVersion,
            byte[] rawTransportBody,
            String normalizedPayload,
            String authenticationMethod,
            String authenticationPrincipalRef,
            UUID correlationUid,
            UUID causationUid,
            TrustedInboxExecutionLane executionLane,
            TrustedInboxScopeResolver scopeResolver) {
        this.sourceNamespace = requirePattern(
                sourceNamespace,
                "sourceNamespace",
                "[a-z][a-z0-9._-]{0,63}");
        this.sourcePrincipalKey = requireBounded(
                sourcePrincipalKey, "sourcePrincipalKey", 160);
        this.externalMessageId = requireBounded(
                externalMessageId, "externalMessageId", 160);
        this.messageKind = requirePattern(
                messageKind, "messageKind", "[A-Z][A-Z0-9_]{0,63}");
        if (normalizedSchemaVersion <= 0) {
            throw new IllegalArgumentException(
                    "normalizedSchemaVersion must be positive");
        }
        this.normalizedSchemaVersion = normalizedSchemaVersion;
        Objects.requireNonNull(rawTransportBody, "rawTransportBody");
        if (rawTransportBody.length == 0 || rawTransportBody.length > MAX_RAW_BYTES) {
            throw new IllegalArgumentException(
                    "rawTransportBody length is outside the trusted inbox bound");
        }
        this.rawTransportBody = Arrays.copyOf(
                rawTransportBody, rawTransportBody.length);
        this.normalizedPayload = requireBounded(
                normalizedPayload, "normalizedPayload", MAX_NORMALIZED_CHARS);
        this.authenticationMethod = requirePattern(
                authenticationMethod,
                "authenticationMethod",
                "[A-Z][A-Z0-9_]{0,31}");
        this.authenticationPrincipalRef = requireBounded(
                authenticationPrincipalRef,
                "authenticationPrincipalRef",
                255);
        this.correlationUid = correlationUid;
        this.causationUid = causationUid;
        this.executionLane = Objects.requireNonNull(executionLane, "executionLane");
        this.scopeResolver = Objects.requireNonNull(
                scopeResolver, "scopeResolver");
    }

    public String sourceNamespace() {
        return sourceNamespace;
    }

    public String sourcePrincipalKey() {
        return sourcePrincipalKey;
    }

    public String externalMessageId() {
        return externalMessageId;
    }

    public String messageKind() {
        return messageKind;
    }

    public int normalizedSchemaVersion() {
        return normalizedSchemaVersion;
    }

    public byte[] rawTransportBody() {
        return Arrays.copyOf(rawTransportBody, rawTransportBody.length);
    }

    public String normalizedPayload() {
        return normalizedPayload;
    }

    public String authenticationMethod() {
        return authenticationMethod;
    }

    public String authenticationPrincipalRef() {
        return authenticationPrincipalRef;
    }

    public UUID correlationUid() {
        return correlationUid;
    }

    public UUID causationUid() {
        return causationUid;
    }

    public TrustedInboxExecutionLane executionLane() {
        return executionLane;
    }

    public TrustedInboxScopeResolver scopeResolver() {
        return scopeResolver;
    }

    private static String requireBounded(
            String value, String field, int maximumLength) {
        Objects.requireNonNull(value, field);
        if (value.isBlank() || value.length() > maximumLength) {
            throw new IllegalArgumentException(
                    field + " must be non-blank and at most "
                            + maximumLength + " characters");
        }
        return value;
    }

    private static String requirePattern(
            String value, String field, String pattern) {
        Objects.requireNonNull(value, field);
        if (!value.matches(pattern)) {
            throw new IllegalArgumentException(
                    field + " does not match its stable identifier format");
        }
        return value;
    }
}
