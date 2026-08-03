package org.enveloping.ecobin.identity.api.port;

import org.enveloping.ecobin.identity.api.query.WalletAdjustmentAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedWalletAdjustment;

/** Revalidates wallet.adjust in the caller-owned writable transaction. */
public interface WalletAdjustmentAuthorizationPort {

    AuthorizedWalletAdjustment authorize(
            WalletAdjustmentAuthorizationQuery query);
}
