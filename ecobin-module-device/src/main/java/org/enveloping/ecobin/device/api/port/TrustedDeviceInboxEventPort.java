package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;

/**
 * Authoritative device event merge Interface. The caller supplies an opaque
 * source-inbox relation issued inside the business transaction.
 */
public interface TrustedDeviceInboxEventPort {

    TrustedDeviceEventApplyResult apply(TrustedDeviceInboxEvent event);
}
