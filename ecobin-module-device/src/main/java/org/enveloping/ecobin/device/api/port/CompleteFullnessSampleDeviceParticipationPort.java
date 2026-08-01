package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;

public interface CompleteFullnessSampleDeviceParticipationPort {

    TrustedDeviceEventApplyResult complete(
            TrustedDeviceInboxEvent event,
            FullnessSampleBusinessWriter businessWriter);
}
