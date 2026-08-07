package org.enveloping.ecobin.device.api.persistence;

import java.util.HashSet;
import java.util.List;
import java.util.Objects;
import java.util.function.Function;

/**
 * Recycling-owned, transaction-bound reference to the internal device keys
 * stored on delivery orders.
 *
 * <p>Implementations must bind the reference to the issuing transaction and
 * thread, permit exactly one consumption and redact {@link Object#toString()}.
 * The keys are exposed only inside the callback so that Device can resolve
 * its own public identities without Recycling joining {@code dev_*} tables.</p>
 */
@FunctionalInterface
public interface DeliveryOrderDeviceFactsRef {

    <T> T withFactKeysOnce(Function<BatchKeys, T> function);

    /**
     * One organization-scoped batch. Caller tokens are only correlation keys
     * and must be unique within the batch.
     */
    record BatchKeys(
            long tenantKey,
            long organizationKey,
            List<FactKey> facts) {

        public BatchKeys {
            positive(tenantKey, "tenantKey");
            positive(organizationKey, "organizationKey");
            facts = List.copyOf(
                    Objects.requireNonNull(facts, "facts"));
            if (facts.isEmpty()) {
                throw new IllegalArgumentException(
                        "facts must not be empty");
            }
            var tokens = new HashSet<String>();
            for (FactKey fact : facts) {
                Objects.requireNonNull(fact, "fact");
                if (!tokens.add(fact.token())) {
                    throw new IllegalArgumentException(
                            "fact tokens must be unique");
                }
            }
        }

        @Override
        public String toString() {
            return "DeliveryOrderDeviceFactsRef.BatchKeys[REDACTED]";
        }
    }

    /**
     * Device-owned foreign keys persisted on one Recycling delivery order.
     */
    record FactKey(
            String token,
            long assetKey,
            long portKey,
            long deliverySessionKey,
            long physicalResultKey) {

        public FactKey {
            if (token == null || token.isBlank()) {
                throw new IllegalArgumentException(
                        "token must not be blank");
            }
            positive(assetKey, "assetKey");
            positive(portKey, "portKey");
            positive(deliverySessionKey, "deliverySessionKey");
            positive(physicalResultKey, "physicalResultKey");
        }

        @Override
        public String toString() {
            return "DeliveryOrderDeviceFactsRef.FactKey[REDACTED]";
        }
    }

    private static long positive(long value, String name) {
        if (value <= 0) {
            throw new IllegalArgumentException(
                    name + " must be positive");
        }
        return value;
    }
}
