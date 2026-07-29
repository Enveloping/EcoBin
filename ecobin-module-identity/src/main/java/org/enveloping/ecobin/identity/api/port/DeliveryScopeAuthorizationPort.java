package org.enveloping.ecobin.identity.api.port;

import org.enveloping.ecobin.identity.api.query.DeliveryScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedDeliveryScope;

/**
 * Revalidates a Web actor and its current delivery capabilities inside the
 * caller-owned transaction.
 */
public interface DeliveryScopeAuthorizationPort {

    AuthorizedDeliveryScope authorize(
            DeliveryScopeAuthorizationQuery query);
}
