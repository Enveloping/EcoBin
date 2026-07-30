package org.enveloping.ecobin.identity.application.persistence;

import org.enveloping.ecobin.identity.api.persistence.DeliveryWalletEntryOwnerRef;

import java.util.UUID;

/**
 * identity 内部签发钱包明细归属用户可信组合的桥。
 */
public interface DeliveryWalletEntryOwnerRefFactory {

    DeliveryWalletEntryOwnerRef issue(
            long tenantKey,
            long organizationKey,
            long organizationUserKey,
            UUID organizationUserUid);
}
