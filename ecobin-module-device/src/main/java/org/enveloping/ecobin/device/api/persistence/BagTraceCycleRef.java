package org.enveloping.ecobin.device.api.persistence;

import java.time.Instant;

/** Recycling-owned cycle boundary consumed only by the device module. */
@FunctionalInterface
public interface BagTraceCycleRef {
    <T> T consumeOnce(CycleFunction<T> function);

    @FunctionalInterface
    interface CycleFunction<T> {
        T apply(
                long tenantKey,
                long organizationKey,
                long portKey,
                Instant startInclusive,
                Instant endExclusive);
    }
}
