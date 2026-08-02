package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;

public interface CompleteFullnessStateChangeDeviceParticipationPort {

    TrustedDeviceEventApplyResult complete(
            TrustedDeviceInboxEvent event,
            FullnessStateChangeBusinessWriter businessWriter);
}
