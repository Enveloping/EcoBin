package org.enveloping.ecobin.identity.api.persistence;

import org.junit.jupiter.api.Test;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.io.Serializable;
import java.lang.reflect.Modifier;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class DeliveryScopePersistenceReferenceTest {

    @Test
    void referenceCanBeConsumedOnlyOnce() {
        try (TransactionFixture ignored = beginTransaction()) {
            DeliveryScopePersistenceRef reference = staffReference(
                    TransactionSynchronizationManager.getResourceMap());

            Keys keys = consume(reference);
            assertEquals(11, keys.tenant());
            assertEquals(22, keys.organization());
            assertNull(keys.platformAdmin());
            assertEquals(44, keys.staffAccount());

            IllegalStateException failure = assertThrows(
                    IllegalStateException.class,
                    () -> consume(reference));
            assertTrue(failure.getMessage().contains("already consumed"));
        }
    }

    @Test
    void referenceCannotCrossTransactions() {
        try (TransactionFixture transaction = beginTransaction()) {
            DeliveryScopePersistenceRef reference = staffReference(
                    TransactionSynchronizationManager.getResourceMap());
            TransactionSynchronizationManager.unbindResource(
                    transaction.resourceKey());
            TransactionSynchronizationManager.bindResource(
                    transaction.resourceKey(),
                    new Object());

            IllegalStateException failure = assertThrows(
                    IllegalStateException.class,
                    () -> consume(reference));

            assertTrue(failure.getMessage().contains("cross transactions"));
        }
    }

    @Test
    void referenceCannotCrossThreads() throws InterruptedException {
        try (TransactionFixture ignored = beginTransaction()) {
            DeliveryScopePersistenceRef reference = staffReference(
                    TransactionSynchronizationManager.getResourceMap());
            AtomicReference<Throwable> failure = new AtomicReference<>();
            Thread otherThread = new Thread(() -> {
                try {
                    consume(reference);
                } catch (Throwable throwable) {
                    failure.set(throwable);
                }
            });

            otherThread.start();
            otherThread.join();

            assertTrue(failure.get() instanceof IllegalStateException);
            assertTrue(failure.get().getMessage().contains("cross threads"));
        }
    }

    @Test
    void referenceExpiresWithItsIssuingTransaction() {
        try (TransactionFixture ignored = beginTransaction()) {
            DeliveryScopePersistenceRef reference = staffReference(
                    TransactionSynchronizationManager.getResourceMap());
            reference.markTransactionCompleted();

            IllegalStateException failure = assertThrows(
                    IllegalStateException.class,
                    () -> consume(reference));

            assertTrue(
                    failure.getMessage().contains("issuing transaction"));
        }
    }

    @Test
    void platformReferenceCarriesOnlyThePlatformActorKey() {
        try (TransactionFixture ignored = beginTransaction()) {
            DeliveryScopePersistenceRef reference =
                    new DeliveryScopePersistenceRef(
                            11,
                            22,
                            33L,
                            null,
                            TransactionSynchronizationManager
                                    .getResourceMap());

            Keys keys = consume(reference);

            assertEquals(33, keys.platformAdmin());
            assertNull(keys.staffAccount());
        }
    }

    @Test
    void referenceIsOpaqueNonSerializableAndRedacted() {
        DeliveryScopePersistenceRef reference = staffReference(
                Map.of(new Object(), new Object()));

        assertEquals(0, reference.getClass().getConstructors().length);
        assertFalse(((Object) reference) instanceof Serializable);
        assertTrue(java.util.Arrays.stream(
                        reference.getClass().getDeclaredFields())
                .allMatch(field ->
                        Modifier.isPrivate(field.getModifiers())));
        assertTrue(java.util.Arrays.stream(
                        reference.getClass().getDeclaredMethods())
                .noneMatch(method ->
                        method.getName().startsWith("get")));
        assertEquals(
                "DeliveryScopePersistenceRef[REDACTED]",
                reference.toString());
        assertFalse(reference.toString().contains("11"));
        assertFalse(reference.toString().contains("22"));
        assertFalse(reference.toString().contains("44"));
    }

    private static DeliveryScopePersistenceRef staffReference(
            Map<Object, Object> resources) {
        return new DeliveryScopePersistenceRef(
                11,
                22,
                null,
                44L,
                resources);
    }

    private static Keys consume(DeliveryScopePersistenceRef reference) {
        return reference.withScopeOnce(Keys::new);
    }

    private static TransactionFixture beginTransaction() {
        Object resourceKey = new Object();
        TransactionSynchronizationManager.setActualTransactionActive(true);
        TransactionSynchronizationManager.bindResource(
                resourceKey,
                new Object());
        return new TransactionFixture(resourceKey);
    }

    private record Keys(
            long tenant,
            long organization,
            Long platformAdmin,
            Long staffAccount) {
    }

    private record TransactionFixture(Object resourceKey)
            implements AutoCloseable {

        @Override
        public void close() {
            if (TransactionSynchronizationManager.hasResource(resourceKey)) {
                TransactionSynchronizationManager.unbindResource(
                        resourceKey);
            }
            TransactionSynchronizationManager
                    .setActualTransactionActive(false);
            TransactionSynchronizationManager
                    .setCurrentTransactionReadOnly(false);
        }
    }
}
