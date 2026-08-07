package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.result.TrustedPlatformConfirmationReceiptEvent;

/** Applies a device receipt for a platform-scoped business confirmation. */
public interface TrustedPlatformConfirmationReceiptPort {

    void apply(TrustedPlatformConfirmationReceiptEvent event);
}
