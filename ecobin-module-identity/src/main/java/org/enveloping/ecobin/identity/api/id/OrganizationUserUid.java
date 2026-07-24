package org.enveloping.ecobin.identity.api.id;

import java.util.Objects;
import java.util.UUID;

public record OrganizationUserUid(UUID value) {
    public OrganizationUserUid {
        Objects.requireNonNull(value, "value");
    }
}
