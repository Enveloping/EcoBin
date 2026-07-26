package org.enveloping.ecobin.identity.application.web;

import org.enveloping.ecobin.framework.context.TrustedAudience;

import java.time.Instant;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;

public record TargetWebActor(
        WebAccountType accountType,
        TrustedAudience audience,
        long principalId,
        UUID principalUid,
        Long tenantId,
        String tenantCode,
        String tenantName,
        UUID sessionUid,
        String displayName,
        String contactPhone,
        long version,
        long authVersion,
        Instant expiresAt,
        Set<String> tenantCapabilities,
        List<OrganizationAccess> organizations) {

    public TargetWebActor {
        tenantCapabilities = Set.copyOf(tenantCapabilities);
        organizations = List.copyOf(organizations);
    }

    public boolean platform() {
        return accountType == WebAccountType.PLATFORM_ADMIN;
    }

    public boolean tenantPrincipal() {
        return accountType == WebAccountType.TENANT_PRINCIPAL;
    }

    public Set<String> effectiveCapabilities() {
        LinkedHashSet<String> result = new LinkedHashSet<>(tenantCapabilities);
        organizations.forEach(org -> result.addAll(org.capabilities()));
        return Set.copyOf(result);
    }

    public boolean hasTenantCapability(String capability) {
        return tenantPrincipal() || tenantCapabilities.contains(capability);
    }

    public boolean hasOrganizationCapability(
            String organizationCode,
            String capability) {
        if (tenantPrincipal() || tenantCapabilities.contains(capability)) {
            return true;
        }
        return organizations.stream()
                .filter(org -> org.organizationCode().equals(organizationCode))
                .anyMatch(org -> org.manager()
                        || org.capabilities().contains(capability));
    }

    public OrganizationAccess organization(String organizationCode) {
        return organizations.stream()
                .filter(org -> org.organizationCode().equals(organizationCode))
                .findFirst()
                .orElse(null);
    }
}
