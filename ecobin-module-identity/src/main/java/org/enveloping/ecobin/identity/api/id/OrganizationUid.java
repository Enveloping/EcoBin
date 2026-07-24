package org.enveloping.ecobin.identity.api.id;

import java.util.Objects;
import java.util.UUID;

public record OrganizationUid(UUID value) {
    public OrganizationUid {
        Objects.requireNonNull(value, "value");
    }
}
