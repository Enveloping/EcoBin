package org.enveloping.ecobin.identity.api.result;

import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.enveloping.ecobin.identity.api.id.SessionUid;
import org.enveloping.ecobin.identity.api.persistence.CleanAuditActorRef;
import org.enveloping.ecobin.identity.api.persistence.CleanOrganizationUserRef;

import java.util.Objects;

/** 已锁定并确认具有清运能力的当前机构用户。 */
public record LockedCleanOrganizationUser(
        OrganizationUserUid organizationUserUid,
        SessionUid loginSessionUid,
        CleanOrganizationUserRef organizationUserRef,
        CleanAuditActorRef auditActorRef) {

    public LockedCleanOrganizationUser {
        Objects.requireNonNull(
                organizationUserUid,
                "organizationUserUid");
        Objects.requireNonNull(loginSessionUid, "loginSessionUid");
        Objects.requireNonNull(
                organizationUserRef,
                "organizationUserRef");
        Objects.requireNonNull(auditActorRef, "auditActorRef");
    }
}
