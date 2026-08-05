package org.enveloping.ecobin.identity.api.result;

import org.enveloping.ecobin.identity.api.persistence.ManagementScopePersistenceRef;

import java.util.List;
import java.util.Objects;
import java.util.UUID;

public record AuthorizedManagementScope(
        boolean platformActor,
        UUID principalUid,
        UUID sessionUid,
        String actorDisplayName,
        String tenantCode,
        boolean tenantWide,
        List<Organization> organizations,
        ManagementScopePersistenceRef persistenceRef) {

    public AuthorizedManagementScope {
        Objects.requireNonNull(principalUid, "principalUid");
        Objects.requireNonNull(sessionUid, "sessionUid");
        Objects.requireNonNull(actorDisplayName, "actorDisplayName");
        organizations = List.copyOf(organizations);
        Objects.requireNonNull(persistenceRef, "persistenceRef");
    }

    public record Organization(String code, String name) {
        public Organization {
            Objects.requireNonNull(code, "code");
            Objects.requireNonNull(name, "name");
        }
    }
}
