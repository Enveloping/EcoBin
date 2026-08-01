package org.enveloping.ecobin.identity.api.port;

import org.enveloping.ecobin.identity.api.persistence.DeliveryWalletEntryOwnerRef;
import org.enveloping.ecobin.identity.api.persistence.DeliveryWalletEntryOwnerRequestRef;

/**
 * 在投递审核事务中核对并签发钱包明细归属用户可信组合。
 */
public interface DeliveryWalletEntryOwnerResolverPort {

    DeliveryWalletEntryOwnerRef resolve(
            DeliveryWalletEntryOwnerRequestRef requestRef);
}
