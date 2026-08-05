package org.enveloping.ecobin.device.api.persistence;

import java.util.UUID;

/** Recycling-owned relationship keys consumed only by the device module. */
@FunctionalInterface
public interface RecyclingDeviceRelationBatchRef {

    void consumeOnce(EntrySink sink);

    interface EntrySink {
        void port(UUID token, long tenantKey, long organizationKey,
                  long portKey);

        void deliverySession(UUID token, long tenantKey,
                             long organizationKey, long sessionKey);

        void fullnessStateFact(UUID token, long tenantKey,
                               long organizationKey, long factKey);
    }
}
