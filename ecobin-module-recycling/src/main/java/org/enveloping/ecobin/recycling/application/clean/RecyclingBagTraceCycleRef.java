package org.enveloping.ecobin.recycling.application.clean;

import org.enveloping.ecobin.device.api.persistence.BagTraceCycleRef;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.time.Instant;

final class RecyclingBagTraceCycleRef implements BagTraceCycleRef {

    private final long tenantId;
    private final long organizationId;
    private final long portId;
    private final Instant start;
    private final Instant end;
    private final long threadId = Thread.currentThread().threadId();
    private boolean consumed;

    RecyclingBagTraceCycleRef(
            long tenantId,
            long organizationId,
            long portId,
            Instant start,
            Instant end) {
        this.tenantId = tenantId;
        this.organizationId = organizationId;
        this.portId = portId;
        this.start = start;
        this.end = end;
    }

    @Override
    public synchronized <T> T consumeOnce(CycleFunction<T> function) {
        if (consumed || threadId != Thread.currentThread().threadId()
                || !TransactionSynchronizationManager
                .isActualTransactionActive()) {
            throw new IllegalStateException(
                    "bag trace cycle reference is no longer valid");
        }
        consumed = true;
        return function.apply(
                tenantId, organizationId, portId, start, end);
    }
}
