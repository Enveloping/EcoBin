package org.enveloping.ecobin.funds.api.port;

import org.enveloping.ecobin.funds.api.query.PersonalWalletEntryAudience;
import org.enveloping.ecobin.funds.api.query.WalletEntryFilter;
import org.enveloping.ecobin.funds.api.result.WalletBalanceSnapshot;
import org.enveloping.ecobin.funds.api.result.WalletEntryPage;
import org.enveloping.ecobin.identity.api.persistence.DeliveryWalletQueryOwnerRef;
import org.enveloping.ecobin.identity.api.persistence.WalletQueryScopeRef;

public interface WalletQueryPort {

    WalletBalanceSnapshot balance(
            DeliveryWalletQueryOwnerRef ownerRef);

    WalletEntryPage personalEntries(
            DeliveryWalletQueryOwnerRef ownerRef,
            PersonalWalletEntryAudience audience,
            String cursor,
            Integer limit);

    WalletEntryPage organizationEntries(
            WalletQueryScopeRef scopeRef,
            WalletEntryFilter filter,
            String cursor,
            Integer limit);
}
