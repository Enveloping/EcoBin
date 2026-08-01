package org.enveloping.ecobin.identity.api.port;

import org.enveloping.ecobin.identity.api.query.DeliveryScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedDeliveryScope;

/**
 * Revalidates a Web actor and its current recycling capabilities inside the
 * caller-owned transaction. The historical type name is retained so existing
 * delivery callers remain source-compatible.
 */
public interface DeliveryScopeAuthorizationPort {

    AuthorizedDeliveryScope authorize(
            DeliveryScopeAuthorizationQuery query);
}
