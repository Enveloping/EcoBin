package org.enveloping.ecobin.recycling.api.port;

import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;

/**
 * Applies one trusted cleaner-confirmed completion as a clean record.
 */
public interface ApplyCleanCompleteUseCase {

    TrustedDeviceEventApplyResult apply(TrustedDeviceInboxEvent event);
}
