package org.enveloping.ecobin.identity.application.web;

import java.util.Set;

public record OrganizationAccess(
        long organizationId,
        String organizationCode,
        String organizationName,
        boolean manager,
        Set<String> capabilities) {

    public OrganizationAccess {
        capabilities = Set.copyOf(capabilities);
    }
}
