package org.enveloping.ecobin.device.api.persistence;

import java.util.function.Function;

/**
 * Relationship-specific, single-use reference to the recycling facts needed
 * by {@code dev_delivery_session}. Implementations must be bound to the
 * issuing thread and transaction and must redact {@link Object#toString()}.
 */
public interface DeliverySessionBusinessFactsRef {

    <T> T withBusinessForeignKeysOnce(
            Function<BusinessForeignKeys, T> function);

    record BusinessForeignKeys(
            long tenantKey,
            long organizationKey,
            long deliveryConfigurationKey,
            long bagKey) {

        public BusinessForeignKeys {
            if (tenantKey <= 0
                    || organizationKey <= 0
                    || deliveryConfigurationKey <= 0
                    || bagKey <= 0) {
                throw new IllegalArgumentException(
                        "business foreign keys must be positive");
            }
        }
    }
}
