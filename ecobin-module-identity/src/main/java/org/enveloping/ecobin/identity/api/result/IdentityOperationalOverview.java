package org.enveloping.ecobin.identity.api.result;

import org.enveloping.ecobin.identity.api.persistence.RegistrationAssetAttributionRef;

import java.util.List;

public record IdentityOperationalOverview(List<Organization> organizations) {
    public IdentityOperationalOverview { organizations = List.copyOf(organizations); }

    public record Organization(
            String organizationCode,
            String organizationName,
            long registeredUserCount,
            long directEntryCount,
            List<RegistrationAssetAttributionRef> byAsset) {
        public Organization { byAsset = List.copyOf(byAsset); }
    }
}
