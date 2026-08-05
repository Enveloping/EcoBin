package org.enveloping.ecobin.recycling.application.clean;

import org.enveloping.ecobin.identity.api.persistence.BagTraceIdentityBatchRef;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.List;
import java.util.UUID;

final class BagTraceIdentityBatch implements BagTraceIdentityBatchRef {

    private final List<Entry> entries;
    private final long threadId = Thread.currentThread().threadId();
    private boolean consumed;

    BagTraceIdentityBatch(List<Entry> entries) {
        this.entries = List.copyOf(entries);
    }

    @Override
    public synchronized void consumeOnce(EntrySink sink) {
        if (consumed || threadId != Thread.currentThread().threadId()
                || !TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "bag trace identity batch is no longer valid");
        }
        consumed = true;
        entries.forEach(entry -> sink.organizationUser(
                entry.token(), entry.tenantId(), entry.organizationId(),
                entry.organizationUserId()));
    }

    record Entry(
            UUID token,
            long tenantId,
            long organizationId,
            long organizationUserId) { }
}
