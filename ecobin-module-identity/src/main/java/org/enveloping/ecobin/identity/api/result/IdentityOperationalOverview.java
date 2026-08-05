package org.enveloping.ecobin.identity.api.result;

import org.enveloping.ecobin.identity.api.persistence.RegistrationDeploymentAttributionRef;

import java.util.List;

public record IdentityOperationalOverview(List<Organization> organizations) {
    public IdentityOperationalOverview { organizations = List.copyOf(organizations); }

    public record Organization(
            String organizationCode,
            String organizationName,
            long registeredUserCount,
            long directEntryCount,
            List<RegistrationDeploymentAttributionRef> byDeployment) {
        public Organization { byDeployment = List.copyOf(byDeployment); }
    }
}
