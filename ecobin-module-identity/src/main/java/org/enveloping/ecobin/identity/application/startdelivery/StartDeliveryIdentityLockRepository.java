package org.enveloping.ecobin.identity.application.startdelivery;

import java.time.Instant;
import java.util.Optional;
import java.util.UUID;

interface StartDeliveryIdentityLockRepository {

    Optional<TenantRow> lockTenant(long tenantId);

    Optional<OrganizationRow> lockOrganization(long organizationId);

    Optional<MiniappRow> lockMiniapp(
            long tenantId,
            long organizationId,
            long miniappId);

    Optional<OrganizationUserRow> lockOrganizationUser(
            long organizationUserId);

    Optional<OrganizationUserSessionRow> lockOrganizationUserSession(
            UUID sessionUid);

    record TenantRow(long id, String tenantCode, String status) {
    }

    record OrganizationRow(
            long id,
            long tenantId,
            String organizationCode,
            String status) {
    }

    record MiniappRow(
            long id,
            long tenantId,
            long organizationId,
            String appId,
            boolean loginEnabled,
            Instant activatedAt) {
    }

    record OrganizationUserRow(
            long id,
            UUID userUid,
            long tenantId,
            long organizationId,
            long miniappId,
            String phoneE164,
            Instant phoneBoundAt,
            String status,
            long authVersion) {
    }

    record OrganizationUserSessionRow(
            UUID sessionUid,
            long tenantId,
            long organizationId,
            long miniappId,
            long organizationUserId,
            Instant issuedAt,
            Instant expiresAt,
            Instant revokedAt,
            long authVersionSnapshot) {
    }
}
