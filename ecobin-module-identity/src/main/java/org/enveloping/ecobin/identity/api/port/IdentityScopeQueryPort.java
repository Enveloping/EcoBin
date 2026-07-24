package org.enveloping.ecobin.identity.api.port;

import org.enveloping.ecobin.identity.api.query.CurrentIdentityScopeQuery;
import org.enveloping.ecobin.identity.api.result.IdentityScope;

public interface IdentityScopeQueryPort {

    IdentityScope current(CurrentIdentityScopeQuery query);
}
