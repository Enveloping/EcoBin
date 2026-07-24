package org.enveloping.ecobin.identity.application.security;

import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.framework.context.TrustedExecutionContext;
import org.enveloping.ecobin.framework.context.TrustedExecutionContextPort;
import org.enveloping.ecobin.identity.api.id.OrganizationUid;
import org.enveloping.ecobin.identity.api.id.PrincipalUid;
import org.enveloping.ecobin.identity.api.id.SessionUid;
import org.enveloping.ecobin.identity.api.id.TenantUid;
import org.enveloping.ecobin.identity.api.port.IdentityScopeQueryPort;
import org.enveloping.ecobin.identity.api.query.CurrentIdentityScopeQuery;
import org.enveloping.ecobin.identity.api.result.IdentityScope;
import org.enveloping.ecobin.identity.api.value.IdentityAudience;
import org.enveloping.ecobin.identity.api.value.IdentityPrincipalKind;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class IdentityScopeQueryService implements IdentityScopeQueryPort {

    private final TrustedExecutionContextPort contextPort;

    @Override
    public IdentityScope current(CurrentIdentityScopeQuery query) {
        TrustedExecutionContext context = contextPort.current();
        return new IdentityScope(
                IdentityPrincipalKind.valueOf(context.principalKind().name()),
                new PrincipalUid(context.principalUid()),
                IdentityAudience.valueOf(context.audience().name()),
                context.tenantUid() == null ? null : new TenantUid(context.tenantUid()),
                context.activeOrganizationUid() == null
                        ? null
                        : new OrganizationUid(context.activeOrganizationUid()),
                new SessionUid(context.sessionJti()),
                context.authVersion(),
                context.requestId());
    }
}
