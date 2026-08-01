package org.enveloping.ecobin.identity.application.persistence;

import org.enveloping.ecobin.identity.api.persistence.DeliveryQueryOrganizationUserRef;
import org.enveloping.ecobin.identity.api.persistence.DeliveryWalletQueryOwnerRef;
import org.enveloping.ecobin.identity.api.persistence.WalletQueryScopeRef;

import java.util.UUID;

/**
 * identity 内部发行当前投递 GET 所需两类只读查询引用的桥。
 */
public interface DeliveryIdentityQueryRefFactory {

    DeliveryQueryOrganizationUserRef issueDeliveryQueryUser(
            long tenantKey,
            long organizationKey,
            long organizationUserKey,
            UUID organizationUserUid);

    DeliveryWalletQueryOwnerRef issueWalletQueryOwner(
            long tenantKey,
            long organizationKey,
            long organizationUserKey,
            UUID organizationUserUid);

    WalletQueryScopeRef issueWalletScope(
            long tenantKey,
            long organizationKey,
            Long organizationUserKey,
            UUID organizationUserUid);
}
