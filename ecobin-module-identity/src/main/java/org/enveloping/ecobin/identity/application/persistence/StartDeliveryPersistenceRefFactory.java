package org.enveloping.ecobin.identity.application.persistence;

import org.enveloping.ecobin.identity.api.persistence.DeliverySessionOrganizationUserRef;
import org.enveloping.ecobin.identity.api.persistence.StartDeliveryAuditActorRef;
import org.enveloping.ecobin.identity.api.persistence.StartDeliveryOrganizationScopeRef;
import org.enveloping.ecobin.identity.api.persistence.StartDeliveryWalletOwnerRef;

/**
 * identity 内部用于发行开始投递关系引用的桥。
 *
 * <p>该类型不属于模块公开 API，其他模块只能接收专用引用。</p>
 */
public interface StartDeliveryPersistenceRefFactory {

    StartDeliveryOrganizationScopeRef issueOrganizationScope(
            long tenantKey,
            long organizationKey);

    StartDeliveryWalletOwnerRef issueWalletOwner(
            long tenantKey,
            long organizationKey,
            long organizationUserKey);

    DeliverySessionOrganizationUserRef issueDeliverySessionUser(
            long tenantKey,
            long organizationKey,
            long organizationUserKey);

    StartDeliveryAuditActorRef issueAuditActor(
            long tenantKey,
            long organizationKey,
            long organizationUserKey);
}
