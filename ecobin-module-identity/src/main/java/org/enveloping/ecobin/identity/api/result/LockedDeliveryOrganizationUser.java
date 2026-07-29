package org.enveloping.ecobin.identity.api.result;

import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.enveloping.ecobin.identity.api.id.SessionUid;
import org.enveloping.ecobin.identity.api.persistence.DeliverySessionOrganizationUserRef;
import org.enveloping.ecobin.identity.api.persistence.StartDeliveryAuditActorRef;
import org.enveloping.ecobin.identity.api.persistence.StartDeliveryWalletOwnerRef;

import java.util.Objects;

/**
 * 已锁定并复核用户、手机号绑定状态和当前登录会话的结果。
 */
public record LockedDeliveryOrganizationUser(
        OrganizationUserUid organizationUserUid,
        SessionUid loginSessionUid,
        StartDeliveryWalletOwnerRef walletOwnerRef,
        DeliverySessionOrganizationUserRef deliverySessionUserRef,
        StartDeliveryAuditActorRef auditActorRef) {

    public LockedDeliveryOrganizationUser {
        Objects.requireNonNull(
                organizationUserUid,
                "organizationUserUid");
        Objects.requireNonNull(loginSessionUid, "loginSessionUid");
        Objects.requireNonNull(walletOwnerRef, "walletOwnerRef");
        Objects.requireNonNull(
                deliverySessionUserRef,
                "deliverySessionUserRef");
        Objects.requireNonNull(auditActorRef, "auditActorRef");
    }
}
