package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.result.DeviceTransportPresenceApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceTransportEvent;

public interface TrustedDeviceTransportPresencePort {

    DeviceTransportPresenceApplyResult apply(
            TrustedDeviceTransportEvent event);

    DeviceTransportPresenceApplyResult observeOutboundOffline(
            String hardwareSn);

    /**
     * Records that an already-authorized reliable device inbox message was
     * received. Callers must invoke this only after consuming the trusted
     * inbox reference and resolving the authoritative hardware identity.
     */
    DeviceTransportPresenceApplyResult observeAuthenticatedMessage(
            String hardwareSn,
            long sourceInboxId);
}
