package org.enveloping.ecobin.identity.api.persistence;

import org.enveloping.ecobin.identity.api.value.DeliveryIdentityFactToken;
import org.junit.jupiter.api.Test;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.io.Serializable;
import java.lang.reflect.Modifier;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class DeliveryOrganizationUserFilterReferenceTest {

    @Test
    void filterReferenceCanBeConsumedOnlyOnce() {
        try (TransactionFixture ignored = beginReadTransaction()) {
            DeliveryOrganizationUserFilterRef reference = reference(
                    TransactionSynchronizationManager.getResourceMap());

            assertEquals(new Keys(11, 22, 33), consume(reference));
            IllegalStateException failure = assertThrows(
                    IllegalStateException.class,
                    () -> consume(reference));

            assertTrue(failure.getMessage().contains("already consumed"));
        }
    }

    @Test
    void filterReferenceCannotCrossTransactions() {
        try (TransactionFixture transaction = beginReadTransaction()) {
            DeliveryOrganizationUserFilterRef reference = reference(
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
    void filterReferenceCannotCrossThreads() throws InterruptedException {
        try (TransactionFixture ignored = beginReadTransaction()) {
            DeliveryOrganizationUserFilterRef reference = reference(
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
    void filterReferenceRejectsAWriteTransaction() {
        try (TransactionFixture ignored = beginWriteTransaction()) {
            DeliveryOrganizationUserFilterRef reference = reference(
                    TransactionSynchronizationManager.getResourceMap());

            IllegalStateException failure = assertThrows(
                    IllegalStateException.class,
                    () -> consume(reference));

            assertTrue(failure.getMessage().contains("read-only"));
        }
    }

    @Test
    void filterReferenceAndCorrelationTokenAreOpaqueAndRedacted() {
        DeliveryOrganizationUserFilterRef reference = reference(
                Map.of(new Object(), new Object()));
        DeliveryIdentityFactToken token =
                DeliveryIdentityFactToken.create();

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
                "DeliveryOrganizationUserFilterRef[REDACTED]",
                reference.toString());
        assertFalse(reference.toString().contains("11"));
        assertFalse(reference.toString().contains("22"));
        assertFalse(reference.toString().contains("33"));

        assertEquals(0, token.getClass().getConstructors().length);
        assertFalse(((Object) token) instanceof Serializable);
        assertTrue(java.util.Arrays.stream(
                        token.getClass().getDeclaredFields())
                .allMatch(field ->
                        Modifier.isPrivate(field.getModifiers())));
        assertTrue(java.util.Arrays.stream(
                        token.getClass().getDeclaredMethods())
                .noneMatch(method ->
                        method.getName().startsWith("get")));
        assertEquals(
                "DeliveryIdentityFactToken[REDACTED]",
                token.toString());
    }

    private static DeliveryOrganizationUserFilterRef reference(
            Map<Object, Object> resources) {
        return new DeliveryOrganizationUserFilterRef(
                11,
                22,
                33,
                resources);
    }

    private static Keys consume(
            DeliveryOrganizationUserFilterRef reference) {
        return reference.withOrganizationUserOnce(Keys::new);
    }

    private static TransactionFixture beginReadTransaction() {
        return beginTransaction(true);
    }

    private static TransactionFixture beginWriteTransaction() {
        return beginTransaction(false);
    }

    private static TransactionFixture beginTransaction(boolean readOnly) {
        Object resourceKey = new Object();
        TransactionSynchronizationManager.setActualTransactionActive(true);
        TransactionSynchronizationManager
                .setCurrentTransactionReadOnly(readOnly);
        TransactionSynchronizationManager.bindResource(
                resourceKey,
                new Object());
        return new TransactionFixture(resourceKey);
    }

    private record Keys(
            long tenant,
            long organization,
            long organizationUser) {
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
