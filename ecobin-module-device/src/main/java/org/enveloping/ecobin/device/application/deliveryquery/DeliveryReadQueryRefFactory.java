package org.enveloping.ecobin.device.application.deliveryquery;

import org.enveloping.ecobin.device.api.persistence.DeliveryOptionsBusinessQueryRef;
import org.enveloping.ecobin.device.api.persistence.DeliverySessionBusinessQueryRef;

import java.util.List;

public interface DeliveryReadQueryRefFactory {

    DeliveryOptionsBusinessQueryRef issueOptionsBusiness(
            long tenantKey,
            long organizationKey,
            long deploymentKey,
            List<DeliveryOptionsBusinessQueryRef.PortKey> ports);

    DeliverySessionBusinessQueryRef issueSessionBusiness(
            long tenantKey,
            long organizationKey,
            long deliverySessionKey);
}
