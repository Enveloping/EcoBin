package org.enveloping.ecobin.identity.api.persistence;

import org.junit.jupiter.api.Test;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.io.Serializable;
import java.lang.reflect.Modifier;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class MaintenanceSshKeyPersistenceReferenceTest {

    @Test
    void writesHiddenKeyOnceInIssuingTransaction() {
        try (TransactionFixture ignored = beginTransaction()) {
            MaintenanceSshKeyPersistenceRef reference = reference(
                    TransactionSynchronizationManager.getResourceMap());
            AtomicLong writtenKey = new AtomicLong();

            reference.writeForeignKeyTo(writtenKey::set);

            assertEquals(73L, writtenKey.get());
            IllegalStateException duplicate = assertThrows(
                    IllegalStateException.class,
                    () -> reference.writeForeignKeyTo(writtenKey::set));
            assertTrue(duplicate.getMessage().contains("already consumed"));
        }
    }

    @Test
    void rejectsUseOutsideIssuingTransaction() {
        MaintenanceSshKeyPersistenceRef reference;
        try (TransactionFixture ignored = beginTransaction()) {
            reference = reference(
                    TransactionSynchronizationManager.getResourceMap());
        }

        IllegalStateException failure = assertThrows(
                IllegalStateException.class,
                () -> reference.writeForeignKeyTo(key -> { }));

        assertTrue(failure.getMessage().contains("issuing transaction"));
    }

    @Test
    void rejectsDifferentBoundTransactionResource() {
        try (TransactionFixture fixture = beginTransaction()) {
            MaintenanceSshKeyPersistenceRef reference = reference(
                    TransactionSynchronizationManager.getResourceMap());
            TransactionSynchronizationManager.unbindResource(
                    fixture.resourceKey());
            TransactionSynchronizationManager.bindResource(
                    fixture.resourceKey(), new Object());

            IllegalStateException failure = assertThrows(
                    IllegalStateException.class,
                    () -> reference.writeForeignKeyTo(key -> { }));

            assertTrue(failure.getMessage().contains("cross transactions"));
        }
    }

    @Test
    void isOpaqueAndDoesNotRevealDatabaseKey() {
        MaintenanceSshKeyPersistenceRef reference = reference(
                Map.of(new Object(), new Object()));

        assertEquals(0, reference.getClass().getConstructors().length);
        assertFalse(((Object) reference) instanceof Serializable);
        assertTrue(java.util.Arrays.stream(
                        reference.getClass().getDeclaredFields())
                .allMatch(field -> Modifier.isPrivate(field.getModifiers())));
        assertTrue(java.util.Arrays.stream(
                        reference.getClass().getDeclaredMethods())
                .noneMatch(method -> method.getName().startsWith("get")));
        assertEquals(
                "MaintenanceSshKeyPersistenceRef[REDACTED]",
                reference.toString());
        assertFalse(reference.toString().contains("73"));
    }

    private static MaintenanceSshKeyPersistenceRef reference(
            Map<Object, Object> resources) {
        return new MaintenanceSshKeyPersistenceRef(73L, resources);
    }

    private static TransactionFixture beginTransaction() {
        Object resourceKey = new Object();
        TransactionSynchronizationManager.setActualTransactionActive(true);
        TransactionSynchronizationManager.bindResource(
                resourceKey, new Object());
        return new TransactionFixture(resourceKey);
    }

    private record TransactionFixture(Object resourceKey)
            implements AutoCloseable {
        @Override
        public void close() {
            if (TransactionSynchronizationManager.hasResource(resourceKey)) {
                TransactionSynchronizationManager.unbindResource(resourceKey);
            }
            TransactionSynchronizationManager
                    .setActualTransactionActive(false);
            TransactionSynchronizationManager
                    .setCurrentTransactionReadOnly(false);
        }
    }
}
