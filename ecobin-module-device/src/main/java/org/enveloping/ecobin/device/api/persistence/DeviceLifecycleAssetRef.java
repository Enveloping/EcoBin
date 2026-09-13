package org.enveloping.ecobin.device.api.persistence;

import java.util.function.LongConsumer;

/** Single-use relation for participation in the locked asset's control transaction. */
public interface DeviceLifecycleAssetRef {
    void withAssetKeyOnce(LongConsumer consumer);
}
