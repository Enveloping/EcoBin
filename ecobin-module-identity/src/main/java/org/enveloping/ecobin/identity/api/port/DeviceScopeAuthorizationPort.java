package org.enveloping.ecobin.identity.api.port;

import org.enveloping.ecobin.identity.api.query.DeviceScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedDeviceScope;

public interface DeviceScopeAuthorizationPort {

    AuthorizedDeviceScope authorize(DeviceScopeAuthorizationQuery query);
}
