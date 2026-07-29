package org.enveloping.ecobin.identity.api.result;

import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.enveloping.ecobin.identity.api.id.PrincipalUid;
import org.enveloping.ecobin.identity.api.value.DeliveryIdentityFactToken;
import org.enveloping.ecobin.identity.api.value.IdentityPrincipalKind;

import java.util.Map;
import java.util.Objects;

public record DeliveryOrderIdentityFacts(
        Map<DeliveryIdentityFactToken, OrganizationUserUid>
                organizationUsers,
        Map<DeliveryIdentityFactToken, ReviewerIdentity>
                reviewers) {

    public DeliveryOrderIdentityFacts {
        organizationUsers = Map.copyOf(
                Objects.requireNonNull(
                        organizationUsers,
                        "organizationUsers"));
        reviewers = Map.copyOf(
                Objects.requireNonNull(reviewers, "reviewers"));
    }

    public record ReviewerIdentity(
            IdentityPrincipalKind actorKind,
            PrincipalUid actorUid,
            String displayName) {

        public ReviewerIdentity {
            Objects.requireNonNull(actorKind, "actorKind");
            if (actorKind != IdentityPrincipalKind.PLATFORM_ADMIN
                    && actorKind
                    != IdentityPrincipalKind.TENANT_PRINCIPAL
                    && actorKind
                    != IdentityPrincipalKind.STAFF_ACCOUNT) {
                throw new IllegalArgumentException(
                        "delivery reviewer must be a Web actor");
            }
            Objects.requireNonNull(actorUid, "actorUid");
            if (displayName == null || displayName.isBlank()) {
                throw new IllegalArgumentException(
                        "displayName must not be blank");
            }
            displayName = displayName.trim();
        }
    }
}
