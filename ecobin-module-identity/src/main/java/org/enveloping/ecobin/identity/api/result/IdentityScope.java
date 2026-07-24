package org.enveloping.ecobin.identity.api.result;

import org.enveloping.ecobin.identity.api.id.OrganizationUid;
import org.enveloping.ecobin.identity.api.id.PrincipalUid;
import org.enveloping.ecobin.identity.api.id.SessionUid;
import org.enveloping.ecobin.identity.api.id.TenantUid;
import org.enveloping.ecobin.identity.api.value.IdentityAudience;
import org.enveloping.ecobin.identity.api.value.IdentityPrincipalKind;

public record IdentityScope(
        IdentityPrincipalKind principalKind,
        PrincipalUid principalUid,
        IdentityAudience audience,
        TenantUid tenantUid,
        OrganizationUid activeOrganizationUid,
        SessionUid sessionUid,
        long authVersion,
        String requestId) {
}
