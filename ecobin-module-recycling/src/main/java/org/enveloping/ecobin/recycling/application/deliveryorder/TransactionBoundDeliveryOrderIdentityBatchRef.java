package org.enveloping.ecobin.recycling.application.deliveryorder;

import org.enveloping.ecobin.identity.api.persistence.DeliveryOrderIdentityBatchRef;
import org.enveloping.ecobin.identity.api.value.DeliveryIdentityFactToken;

import java.util.List;
import java.util.Objects;

final class TransactionBoundDeliveryOrderIdentityBatchRef
        implements DeliveryOrderIdentityBatchRef {

    private final List<OrganizationUserEntry> users;
    private final List<ReviewerEntry> reviewers;
    private final DeliveryOrderTransactionRefGuard guard;

    private TransactionBoundDeliveryOrderIdentityBatchRef(
            List<OrganizationUserEntry> users,
            List<ReviewerEntry> reviewers) {
        this.users = List.copyOf(users);
        this.reviewers = List.copyOf(reviewers);
        guard = DeliveryOrderTransactionRefGuard.issue();
    }

    static TransactionBoundDeliveryOrderIdentityBatchRef issue(
            List<OrganizationUserEntry> users,
            List<ReviewerEntry> reviewers) {
        Objects.requireNonNull(users, "users");
        Objects.requireNonNull(reviewers, "reviewers");
        if (users.isEmpty() && reviewers.isEmpty()) {
            throw new IllegalArgumentException(
                    "identity fact batch must not be empty");
        }
        return new TransactionBoundDeliveryOrderIdentityBatchRef(
                users,
                reviewers);
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
        reviewers.forEach(entry -> sink.reviewer(
                entry.token(),
                entry.tenantId(),
                entry.organizationId(),
                entry.reviewerKind(),
                entry.platformAdminId(),
                entry.staffAccountId()));
    }

    @Override
    public String toString() {
        return "DeliveryOrderIdentityBatchRef[REDACTED]";
    }

    record OrganizationUserEntry(
            DeliveryIdentityFactToken token,
            long tenantId,
            long organizationId,
            long organizationUserId) {

        OrganizationUserEntry {
            Objects.requireNonNull(token, "token");
            requirePositive(tenantId, "tenantId");
            requirePositive(organizationId, "organizationId");
            requirePositive(
                    organizationUserId,
                    "organizationUserId");
        }
    }

    record ReviewerEntry(
            DeliveryIdentityFactToken token,
            long tenantId,
            long organizationId,
            ReviewerKind reviewerKind,
            Long platformAdminId,
            Long staffAccountId) {

        ReviewerEntry {
            Objects.requireNonNull(token, "token");
            requirePositive(tenantId, "tenantId");
            requirePositive(organizationId, "organizationId");
            Objects.requireNonNull(reviewerKind, "reviewerKind");
            if ((platformAdminId == null) == (staffAccountId == null)) {
                throw new IllegalArgumentException(
                        "exactly one reviewer key is required");
            }
            if (platformAdminId != null) {
                requirePositive(platformAdminId, "platformAdminId");
            }
            if (staffAccountId != null) {
                requirePositive(staffAccountId, "staffAccountId");
            }
        }
    }

    private static void requirePositive(long value, String name) {
        if (value <= 0) {
            throw new IllegalArgumentException(name + " must be positive");
        }
    }
}
