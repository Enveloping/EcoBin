package org.enveloping.ecobin.identity.application.deliveryorder;

import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;

import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

interface DeliveryOrderIdentityRepository {

    Map<Long, OrganizationScopeRow> findOrganizationScopes(
            Set<Long> organizationIds);

    Map<Long, OrganizationUserRow> findOrganizationUsers(
            Set<Long> organizationUserIds);

    Map<Long, PlatformAdminRow> findPlatformAdministrators(
            Set<Long> platformAdminIds);

    Map<Long, StaffAccountRow> findStaffAccounts(
            Set<Long> staffAccountIds);

    Optional<OrganizationUserFilterRow> findOrganizationUserFilter(
            String tenantCode,
            String organizationCode,
            OrganizationUserUid organizationUserUid);

    record OrganizationScopeRow(
            long tenantId,
            long organizationId) {
    }

    record OrganizationUserRow(
            long tenantId,
            long organizationId,
            long organizationUserId,
            UUID organizationUserUid) {
    }

    record PlatformAdminRow(
            long platformAdminId,
            UUID platformAdminUid,
            String displayName) {
    }

    record StaffAccountRow(
            long tenantId,
            long staffAccountId,
            UUID staffAccountUid,
            String accountKind,
            String displayName) {
    }

    record OrganizationUserFilterRow(
            long tenantId,
            long organizationId,
            long organizationUserId,
            String tenantCode,
            String organizationCode,
            UUID organizationUserUid) {
    }
}
