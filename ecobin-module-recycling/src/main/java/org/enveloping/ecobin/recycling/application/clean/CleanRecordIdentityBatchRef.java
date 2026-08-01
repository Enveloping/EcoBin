package org.enveloping.ecobin.recycling.application.clean;

import org.enveloping.ecobin.identity.api.persistence.DeliveryOrderIdentityBatchRef;
import org.enveloping.ecobin.identity.api.value.DeliveryIdentityFactToken;

import java.util.List;
import java.util.Objects;

final class CleanRecordIdentityBatchRef
        implements DeliveryOrderIdentityBatchRef {

    private final List<UserEntry> users;
    private final List<ActorEntry> actors;
    private final CleanRecordTransactionRefGuard guard;

    private CleanRecordIdentityBatchRef(
            List<UserEntry> users,
            List<ActorEntry> actors) {
        this.users = List.copyOf(users);
        this.actors = List.copyOf(actors);
        guard = CleanRecordTransactionRefGuard.issue();
    }

    static CleanRecordIdentityBatchRef issue(
            List<UserEntry> users,
            List<ActorEntry> actors) {
        Objects.requireNonNull(users, "users");
        Objects.requireNonNull(actors, "actors");
        if (users.isEmpty() && actors.isEmpty()) {
            throw new IllegalArgumentException(
                    "clean identity fact batch must not be empty");
        }
        return new CleanRecordIdentityBatchRef(users, actors);
    }

    @Override
    public void consumeOnce(EntrySink sink) {
        Objects.requireNonNull(sink, "sink");
        guard.claimOnce();
        users.forEach(entry -> sink.organizationUser(
                entry.token(),
                entry.tenantId(),
                entry.organizationId(),
                entry.organizationUserId()));
        actors.forEach(entry -> sink.reviewer(
                entry.token(),
                entry.tenantId(),
                entry.organizationId(),
                entry.kind(),
                entry.platformAdminId(),
                entry.staffAccountId()));
    }

    @Override
    public String toString() {
        return "CleanRecordIdentityBatchRef[REDACTED]";
    }

    record UserEntry(
            DeliveryIdentityFactToken token,
            long tenantId,
            long organizationId,
            long organizationUserId) {

        UserEntry {
            Objects.requireNonNull(token, "token");
            positive(tenantId, "tenantId");
            positive(organizationId, "organizationId");
            positive(organizationUserId, "organizationUserId");
        }
    }

    record ActorEntry(
            DeliveryIdentityFactToken token,
            long tenantId,
            long organizationId,
            ReviewerKind kind,
            Long platformAdminId,
            Long staffAccountId) {

        ActorEntry {
            Objects.requireNonNull(token, "token");
            positive(tenantId, "tenantId");
            positive(organizationId, "organizationId");
            Objects.requireNonNull(kind, "kind");
            if ((platformAdminId == null) == (staffAccountId == null)) {
                throw new IllegalArgumentException(
                        "exactly one clean change actor key is required");
            }
        }
    }

    private static void positive(long value, String name) {
        if (value <= 0) {
            throw new IllegalArgumentException(
                    name + " must be positive");
        }
    }
}
