package org.enveloping.ecobin.operations.application.governance;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.framework.idempotency.GlobalOperationDigests;
import org.enveloping.ecobin.identity.api.result.AuthorizedManagementScope;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.UUID;

final class GovernanceIdempotency {

    private GovernanceIdempotency() { }

    static void requireVersionFour(UUID value) {
        if (value == null || value.version() != 4) {
            throw new TargetApiException(
                    400, "COMMON.INVALID_IDEMPOTENCY_KEY",
                    "Idempotency-Key 必须是 UUID v4");
        }
    }

    static String requestDigest(Object... values) {
        List<String> canonical = new ArrayList<>(values.length);
        for (Object value : values) {
            canonical.add(value == null ? "" : value.toString());
        }
        return GlobalOperationDigests.sha256(canonical.toArray());
    }

    static String scopeDigest(AuthorizedManagementScope scope) {
        List<String> canonical = new ArrayList<>();
        canonical.add(scope.platformActor() ? "PLATFORM" : "STAFF");
        canonical.add(scope.tenantCode() == null ? "" : scope.tenantCode());
        canonical.add(Boolean.toString(scope.tenantWide()));
        scope.organizations().stream()
                .map(AuthorizedManagementScope.Organization::code)
                .sorted(Comparator.naturalOrder())
                .forEach(canonical::add);
        return GlobalOperationDigests.sha256(canonical.toArray());
    }
}
