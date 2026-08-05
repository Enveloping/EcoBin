package org.enveloping.ecobin.identity.api.persistence;

import org.junit.jupiter.api.Test;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.io.Serializable;
import java.lang.reflect.Modifier;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ManagementScopePersistenceReferenceTest {

    @Test
    void eachNamedConsumerCanResolveTheSameScopeExactlyOnce() {
        try (TransactionFixture ignored = beginTransaction()) {
            ManagementScopePersistenceRef reference = reference(
                    TransactionSynchronizationManager.getResourceMap());

            assertEquals(
                    new Keys(11, 22, "org-a", 33L, null),
                    consume(reference,
                            ManagementScopePersistenceRef.Purpose
                                    .IDENTITY_OPERATIONAL_OVERVIEW));
            assertEquals(
                    new Keys(11, 22, "org-a", 33L, null),
                    consume(reference,
                            ManagementScopePersistenceRef.Purpose
                                    .DEVICE_OPERATIONAL_OVERVIEW));

            IllegalStateException duplicate = assertThrows(
                    IllegalStateException.class,
                    () -> consume(reference,
                            ManagementScopePersistenceRef.Purpose
                                    .DEVICE_OPERATIONAL_OVERVIEW));
            assertTrue(duplicate.getMessage().contains("already consumed"));
            assertThrows(IllegalStateException.class,
                    () -> reference.withScopeOnce(
                            (tenant, organizations, platform, staff) -> null));
        }
    }

    @Test
    void ordinaryObjectIsOpaqueAndDoesNotExposeDatabaseKeys() {
        ManagementScopePersistenceRef reference = reference(
                Map.of(new Object(), new Object()));

        assertEquals(0, reference.getClass().getConstructors().length);
        assertFalse(((Object) reference) instanceof Serializable);
        assertTrue(java.util.Arrays.stream(
                        reference.getClass().getDeclaredFields())
                .allMatch(field -> Modifier.isPrivate(field.getModifiers())));
        assertTrue(java.util.Arrays.stream(
                        reference.getClass().getDeclaredMethods())
                .noneMatch(method -> method.getName().startsWith("get")));
        assertEquals("ManagementScopePersistenceRef[REDACTED]",
                reference.toString());
        assertFalse(reference.toString().contains("11"));
        assertFalse(reference.toString().contains("22"));
    }

    private static ManagementScopePersistenceRef reference(
            Map<Object, Object> resources) {
        return new ManagementScopePersistenceRef(
                11L, List.of(22L), List.of("org-a"),
                33L, null, resources);
    }

    private static Keys consume(
            ManagementScopePersistenceRef reference,
            ManagementScopePersistenceRef.Purpose purpose) {
        return reference.withScopeOnce(
                purpose,
                (tenant, organizations, platform, staff) -> new Keys(
                        tenant,
                        organizations.getFirst().value(),
                        organizations.getFirst().code(),
                        platform,
                        staff));
    }

    private static TransactionFixture beginTransaction() {
        Object resourceKey = new Object();
        TransactionSynchronizationManager.setActualTransactionActive(true);
        TransactionSynchronizationManager.bindResource(
                resourceKey, new Object());
        return new TransactionFixture(resourceKey);
    }

    private record Keys(
            long tenant,
            long organization,
            String organizationCode,
            Long platformAdmin,
            Long staffAccount) { }

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
