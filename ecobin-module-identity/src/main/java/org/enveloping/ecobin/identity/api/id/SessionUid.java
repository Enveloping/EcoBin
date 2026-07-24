package org.enveloping.ecobin.identity.api.id;

import java.util.Objects;
import java.util.UUID;

public record SessionUid(UUID value) {
    public SessionUid {
        Objects.requireNonNull(value, "value");
    }
}
