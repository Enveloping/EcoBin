package org.enveloping.ecobin.identity.api.id;

import java.util.Objects;
import java.util.UUID;

public record PrincipalUid(UUID value) {
    public PrincipalUid {
        Objects.requireNonNull(value, "value");
    }
}
