package org.enveloping.ecobin.identity.application.security;

import java.util.Optional;
import java.util.Set;
import java.util.UUID;

interface DeliveryScopeAuthorizationRepository {

    Optional<PlatformActor> findPlatformActor(
            long platformAdminId,
            UUID platformAdminUid,
            boolean forUpdate);

    Optional<StaffActor> findStaffActor(
            long staffAccountId,
            UUID staffAccountUid,
            boolean forUpdate);

    Optional<Scope> findPlatformScope(
            String tenantCode,
            String organizationCode,
            boolean forUpdate);

    Optional<Scope> findStaffScope(
            long tenantId,
            String organizationCode,
            boolean forUpdate);

    Set<String> findTenantDeliveryCapabilities(
            long tenantId,
            long staffAccountId);

    Optional<Membership> findMembership(
            long tenantId,
            long organizationId,
            long staffAccountId);

    Set<String> findOrganizationDeliveryCapabilities(
            long tenantId,
            long organizationId,
            long staffAccountId);

    Optional<OrganizationUser> findOrganizationUser(
            long tenantId,
            long organizationId,
            UUID organizationUserUid,
            boolean forUpdate);

    record PlatformActor(
            long id,
            String displayName,
            boolean enabled,
            long authVersion) {
    }

    record StaffActor(
            long id,
            long tenantId,
            String accountKind,
            String displayName,
            boolean enabled,
            long authVersion,
            String tenantCode,
            boolean tenantEnabled) {
    }

    record Scope(
            long tenantId,
            String tenantCode,
            long organizationId,
            String organizationCode,
            boolean organizationEnabled) {
    }

    record Membership(boolean manager, boolean enabled) {
    }

    record OrganizationUser(long id, UUID uid) {
    }
}
