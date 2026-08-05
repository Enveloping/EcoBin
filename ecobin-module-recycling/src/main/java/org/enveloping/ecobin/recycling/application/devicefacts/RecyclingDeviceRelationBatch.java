package org.enveloping.ecobin.recycling.application.devicefacts;

import org.enveloping.ecobin.device.api.persistence.RecyclingDeviceRelationBatchRef;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.List;
import java.util.UUID;

public final class RecyclingDeviceRelationBatch
        implements RecyclingDeviceRelationBatchRef {

    private final List<Entry> entries;
    private final long threadId = Thread.currentThread().threadId();
    private boolean consumed;

    public RecyclingDeviceRelationBatch(List<Entry> entries) {
        this.entries = List.copyOf(entries);
    }

    @Override
    public synchronized void consumeOnce(EntrySink sink) {
        if (consumed || threadId != Thread.currentThread().threadId()
                || !TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "recycling device batch is no longer valid");
        }
        consumed = true;
        for (Entry entry : entries) {
            switch (entry.kind()) {
                case PORT -> sink.port(entry.token(), entry.tenantKey(),
                        entry.organizationKey(), entry.relationKey());
                case DELIVERY_SESSION -> sink.deliverySession(
                        entry.token(), entry.tenantKey(),
                        entry.organizationKey(), entry.relationKey());
                case FULLNESS_STATE_FACT -> sink.fullnessStateFact(
                        entry.token(), entry.tenantKey(),
                        entry.organizationKey(), entry.relationKey());
            }
        }
    }

    public record Entry(
            UUID token,
            Kind kind,
            long tenantKey,
            long organizationKey,
            long relationKey) { }

    public enum Kind {
        PORT,
        DELIVERY_SESSION,
        FULLNESS_STATE_FACT
    }
}
