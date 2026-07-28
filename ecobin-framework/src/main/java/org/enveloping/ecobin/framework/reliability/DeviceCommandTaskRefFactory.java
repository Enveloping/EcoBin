package org.enveloping.ecobin.framework.reliability;

public interface DeviceCommandTaskRefFactory {

    DeviceCommandTaskRef issue(
            long tenantKey,
            long organizationKey,
            long deploymentKey,
            long commandKey);
}
