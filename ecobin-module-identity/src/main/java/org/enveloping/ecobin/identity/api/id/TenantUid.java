package org.enveloping.ecobin.identity.api.id;

import java.util.Objects;
import java.util.UUID;

public record TenantUid(UUID value) {
    public TenantUid {
        Objects.requireNonNull(value, "value");
    }
}
