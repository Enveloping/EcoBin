package org.enveloping.ecobin.identity.api.value;

import java.util.Objects;
import java.util.UUID;

/**
 * In-process correlation token for a caller-owned delivery fact.
 *
 * <p>The token intentionally exposes no underlying value and must never be
 * persisted, serialized or used as a business identity.</p>
 */
public final class DeliveryIdentityFactToken {

    private final UUID value;

    private DeliveryIdentityFactToken(UUID value) {
        this.value = Objects.requireNonNull(value, "value");
    }

    public static DeliveryIdentityFactToken create() {
        return new DeliveryIdentityFactToken(UUID.randomUUID());
    }

    @Override
    public boolean equals(Object candidate) {
        return this == candidate
                || candidate instanceof DeliveryIdentityFactToken other
                && value.equals(other.value);
    }

    @Override
    public int hashCode() {
        return value.hashCode();
    }

    @Override
    public String toString() {
        return "DeliveryIdentityFactToken[REDACTED]";
    }
}
