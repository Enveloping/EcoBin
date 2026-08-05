package org.enveloping.ecobin.operations.application.governance;

import org.enveloping.ecobin.identity.api.persistence.GovernanceIdentityBatchRef;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.List;
import java.util.UUID;

final class GovernanceIdentityBatch implements GovernanceIdentityBatchRef {

    private final List<Entry> entries;
    private final long threadId = Thread.currentThread().threadId();
    private boolean consumed;

    GovernanceIdentityBatch(List<Entry> entries) {
        this.entries = List.copyOf(entries);
    }

    @Override
    public synchronized void consumeOnce(EntrySink sink) {
        if (consumed || threadId != Thread.currentThread().threadId()
                || !TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "governance identity batch is no longer valid");
        }
        consumed = true;
        entries.forEach(entry -> sink.entry(
                entry.token(), entry.tenantKey(), entry.organizationKey(),
                entry.actorKind(), entry.platformAdminKey(),
                entry.staffAccountKey(), entry.organizationUserKey()));
    }

    record Entry(
            UUID token,
            Long tenantKey,
            Long organizationKey,
            String actorKind,
            Long platformAdminKey,
            Long staffAccountKey,
            Long organizationUserKey) { }
}
