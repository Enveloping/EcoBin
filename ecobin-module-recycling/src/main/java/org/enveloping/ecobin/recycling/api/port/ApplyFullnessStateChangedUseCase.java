package org.enveloping.ecobin.recycling.api.port;

import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;

public interface ApplyFullnessStateChangedUseCase {

    TrustedDeviceEventApplyResult apply(TrustedDeviceInboxEvent event);
}
