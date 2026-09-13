package org.enveloping.ecobin.identity.web.v1.auth;

import org.enveloping.ecobin.identity.application.web.TargetWebActor;

import java.time.Instant;
import java.util.Comparator;
import java.util.List;
import java.util.UUID;

public record WebSessionView(
        UUID sessionUid,
        String accountType,
        UUID subjectUid,
        String displayName,
        String contactPhone,
        String tenantCode,
        List<String> capabilities,
        List<String> tenantCapabilities,
        List<OrganizationSummaryView> organizations,
        Instant expiresAt,
        long version,
        long authVersion) {

    public static WebSessionView from(TargetWebActor actor) {
        return new WebSessionView(
                actor.sessionUid(),
                actor.accountType().name(),
                actor.principalUid(),
                actor.displayName(),
                actor.contactPhone(),
                actor.tenantCode(),
                actor.effectiveCapabilities().stream().sorted().toList(),
                actor.tenantCapabilities().stream().sorted().toList(),
                actor.organizations().stream()
                        .map(org -> new OrganizationSummaryView(
                                org.organizationCode(),
                                org.organizationName()))
                        .sorted(Comparator.comparing(
                                OrganizationSummaryView::organizationCode))
                        .toList(),
                actor.expiresAt(),
                actor.version(),
                actor.authVersion());
    }
}
