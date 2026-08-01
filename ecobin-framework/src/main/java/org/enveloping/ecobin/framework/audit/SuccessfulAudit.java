package org.enveloping.ecobin.framework.audit;

import java.util.UUID;

public record SuccessfulAudit(
        UUID operationUid,
        AuditActorKind actorKind,
        Long platformAdminId,
        Long staffAccountId,
        Long organizationUserId,
        AuditScopeKind scopeKind,
        Long tenantId,
        Long organizationId,
        String actionCode,
        String targetType,
        String targetStableKey,
        String safeChangeSummaryJson) {
}
