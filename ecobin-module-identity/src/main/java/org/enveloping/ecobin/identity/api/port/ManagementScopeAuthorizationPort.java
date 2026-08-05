package org.enveloping.ecobin.identity.api.port;

import org.enveloping.ecobin.identity.api.query.ManagementScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedManagementScope;

public interface ManagementScopeAuthorizationPort {

    AuthorizedManagementScope authorize(
            ManagementScopeAuthorizationQuery query);
}
