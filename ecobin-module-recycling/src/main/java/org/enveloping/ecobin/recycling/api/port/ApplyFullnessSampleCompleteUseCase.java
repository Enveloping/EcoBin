package org.enveloping.ecobin.recycling.api.port;

import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;

public interface ApplyFullnessSampleCompleteUseCase {

    TrustedDeviceEventApplyResult apply(
            TrustedDeviceInboxEvent event);
}
