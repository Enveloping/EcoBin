package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.framework.reliability.TrustedInboxScopeResolver;

/**
 * Device-owned authority for mapping an authenticated OneNet device identity
 * and immutable deployment code to an organization scope.
 */
public interface TrustedDeviceSourceScopePort {

    TrustedInboxScopeResolver resolverFor(
            String hardwareSn,
            String deploymentCode);
}
