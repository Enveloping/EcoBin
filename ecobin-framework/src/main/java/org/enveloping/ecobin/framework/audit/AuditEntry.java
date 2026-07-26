package org.enveloping.ecobin.framework.audit;

import java.time.Instant;
import java.util.Objects;
import java.util.UUID;

/**
 * A safe, append-only audit fact. Secret material and raw request bodies are
 * deliberately absent from this technical port.
 */
public record AuditEntry(
        UUID auditUid,
        UUID requestUid,
        UUID operationUid,
        AuditScopeKind scopeKind,
        Long tenantId,
        Long organizationId,
        AuditActorKind actorKind,
        Long platformAdminId,
        Long staffAccountId,
        String systemActorCode,
        String actorDisplaySnapshot,
        String actionCode,
        String targetType,
        String targetStableKey,
        String entryChannel,
        String result,
        UUID sessionUid,
        String reason,
        String safeChangeSummaryJson,
        Instant occurredAt) {

    public AuditEntry {
        Objects.requireNonNull(auditUid, "auditUid");
        Objects.requireNonNull(requestUid, "requestUid");
        Objects.requireNonNull(scopeKind, "scopeKind");
        Objects.requireNonNull(actorKind, "actorKind");
        Objects.requireNonNull(actionCode, "actionCode");
        Objects.requireNonNull(targetType, "targetType");
        Objects.requireNonNull(targetStableKey, "targetStableKey");
        Objects.requireNonNull(entryChannel, "entryChannel");
        Objects.requireNonNull(result, "result");
        Objects.requireNonNull(occurredAt, "occurredAt");
    }
}
