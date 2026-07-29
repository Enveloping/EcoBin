package org.enveloping.ecobin.framework.reliability;

public interface DeviceDeploymentTaskRefFactory {

    DeviceDeploymentTaskRef issue(
            long tenantKey,
            long organizationKey,
            long deploymentKey);
}
