package org.enveloping.ecobin.device.application.deliveryquery;

import org.enveloping.ecobin.device.api.persistence.DeliveryOptionsBusinessQueryRef;
import org.enveloping.ecobin.device.api.persistence.DeliveryOrderDeviceFilterRef;
import org.enveloping.ecobin.device.api.persistence.DeliverySessionBusinessQueryRef;

import java.util.List;

public interface DeliveryReadQueryRefFactory {

    DeliveryOptionsBusinessQueryRef issueOptionsBusiness(
            long tenantKey,
            long organizationKey,
            long assetKey,
            List<DeliveryOptionsBusinessQueryRef.PortKey> ports);

    DeliverySessionBusinessQueryRef issueSessionBusiness(
            long tenantKey,
            long organizationKey,
            long deliverySessionKey);

    DeliveryOrderDeviceFilterRef issueOrderFilter(
            long tenantKey,
            long organizationKey,
            Long assetKey,
            List<Long> portKeys);
}
