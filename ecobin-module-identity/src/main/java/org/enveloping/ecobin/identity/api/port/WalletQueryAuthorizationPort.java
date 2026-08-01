package org.enveloping.ecobin.identity.api.port;

import org.enveloping.ecobin.identity.api.query.WalletScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedWalletScope;

/**
 * Revalidates Web wallet.read access in the caller-owned read transaction.
 */
public interface WalletQueryAuthorizationPort {

    AuthorizedWalletScope authorize(
            WalletScopeAuthorizationQuery query);
}
