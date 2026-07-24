package org.enveloping.ecobin.identity.api.command;

import org.enveloping.ecobin.identity.api.id.OrganizationUid;
import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.enveloping.ecobin.identity.api.id.TenantUid;
import org.enveloping.ecobin.identity.api.persistence.OrganizationUserWalletOwnerRef;

import java.time.Instant;
import java.util.Objects;

public record OrganizationUserRegistrationCommand(
        TenantUid tenantUid,
        OrganizationUid organizationUid,
        OrganizationUserUid organizationUserUid,
        Instant registeredAt,
        OrganizationUserWalletOwnerRef walletOwnerRef) {

    public OrganizationUserRegistrationCommand {
        Objects.requireNonNull(tenantUid, "tenantUid");
        Objects.requireNonNull(organizationUid, "organizationUid");
        Objects.requireNonNull(organizationUserUid, "organizationUserUid");
        Objects.requireNonNull(registeredAt, "registeredAt");
        Objects.requireNonNull(walletOwnerRef, "walletOwnerRef");
    }
}
