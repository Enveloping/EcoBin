package org.enveloping.ecobin.identity.api.result;

import org.enveloping.ecobin.identity.api.persistence.FactoryOperatorPersistenceRef;

import java.util.Objects;
import java.util.Set;
import java.util.UUID;

/** Safe, request-local factory mini-program identity. */
public record AuthorizedFactoryOperatorIdentity(
        UUID factoryOperatorUid,
        String operatorCode,
        UUID sessionUid,
        String displayName,
        Set<String> capabilities,
        FactoryOperatorPersistenceRef persistenceRef) {

    public AuthorizedFactoryOperatorIdentity {
        Objects.requireNonNull(factoryOperatorUid, "factoryOperatorUid");
        Objects.requireNonNull(operatorCode, "operatorCode");
        Objects.requireNonNull(sessionUid, "sessionUid");
        Objects.requireNonNull(displayName, "displayName");
        capabilities = Set.copyOf(capabilities);
        Objects.requireNonNull(persistenceRef, "persistenceRef");
    }
}
