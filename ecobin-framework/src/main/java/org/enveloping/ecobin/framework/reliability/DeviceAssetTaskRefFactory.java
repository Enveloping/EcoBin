package org.enveloping.ecobin.framework.reliability;

public interface DeviceAssetTaskRefFactory {

    DeviceAssetTaskRef issue(
            long tenantKey,
            long organizationKey,
            long assetKey);
}
