package org.enveloping.ecobin.device.api.persistence;

import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.time.Instant;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Function;

/** Device-owned session selection consumed only by one bag trace query. */
public final class BagTraceSessionSelectionRef {

    private final List<Session> sessions;
    private final long threadId = Thread.currentThread().threadId();
    private final Map<Object, Object> resources;
    private boolean consumed;
    private boolean completed;

    BagTraceSessionSelectionRef(
            List<Session> sessions,
            Map<Object, Object> resources) {
        this.sessions = List.copyOf(sessions);
        this.resources = new IdentityHashMap<>(resources);
    }

    public synchronized <T> T consumeOnce(Function<List<Session>, T> function) {
        if (consumed || completed || threadId != Thread.currentThread().threadId()
                || !TransactionSynchronizationManager
                .isActualTransactionActive()
                || resources.entrySet().stream().anyMatch(entry ->
                TransactionSynchronizationManager.getResource(entry.getKey())
                        != entry.getValue())) {
            throw new IllegalStateException(
                    "bag trace session selection is no longer valid");
        }
        consumed = true;
        return function.apply(sessions);
    }

    synchronized void markCompleted() {
        completed = true;
    }

    @Override
    public String toString() {
        return "BagTraceSessionSelectionRef[REDACTED]";
    }

    public record Session(long key, Instant createdAt) { }
}
