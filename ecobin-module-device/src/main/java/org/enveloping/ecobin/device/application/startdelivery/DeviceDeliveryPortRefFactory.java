package org.enveloping.ecobin.device.application.startdelivery;

import org.enveloping.ecobin.device.api.persistence.DeviceDeliveryPortRef;

public interface DeviceDeliveryPortRefFactory {

    DeviceDeliveryPortRef issue(
            long tenantKey,
            long organizationKey,
            long deploymentKey,
            long portKey);
}
