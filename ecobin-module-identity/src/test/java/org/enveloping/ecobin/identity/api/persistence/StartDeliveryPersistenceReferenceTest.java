package org.enveloping.ecobin.identity.api.persistence;

import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.io.Serializable;
import java.lang.reflect.Modifier;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class StartDeliveryPersistenceReferenceTest {

    private static final UUID USER_UID =
            UUID.fromString("8e8ebf0e-1111-4111-8111-111111111111");

    @ParameterizedTest
    @EnumSource(ReferenceKind.class)
    void eachReferenceCanBeConsumedOnlyOnce(ReferenceKind kind) {
        try (TransactionFixture ignored =
                     beginTransaction(kind.readOnlyRequired())) {
            Object reference = kind.create(
                    TransactionSynchronizationManager.getResourceMap());

            assertArrayEquals(kind.expectedKeys(), kind.consume(reference));
            IllegalStateException failure = assertThrows(
                    IllegalStateException.class,
                    () -> kind.consume(reference));

            assertTrue(failure.getMessage().contains("already consumed"));
        }
    }

    @ParameterizedTest
    @EnumSource(ReferenceKind.class)
    void eachReferenceCannotCrossThreads(ReferenceKind kind)
            throws InterruptedException {
        try (TransactionFixture ignored =
                     beginTransaction(kind.readOnlyRequired())) {
            Object reference = kind.create(
                    TransactionSynchronizationManager.getResourceMap());
            AtomicReference<Throwable> failure = new AtomicReference<>();
            Thread otherThread = new Thread(() -> {
                try {
                    kind.consume(reference);
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

    @ParameterizedTest
    @EnumSource(ReferenceKind.class)
    void eachReferenceCannotCrossTransactions(ReferenceKind kind) {
        try (TransactionFixture transaction =
                     beginTransaction(kind.readOnlyRequired())) {
            Object reference = kind.create(
                    TransactionSynchronizationManager.getResourceMap());
            TransactionSynchronizationManager.unbindResource(
                    transaction.resourceKey());
            TransactionSynchronizationManager.bindResource(
                    transaction.resourceKey(),
                    new Object());

            IllegalStateException failure = assertThrows(
                    IllegalStateException.class,
                    () -> kind.consume(reference));

            assertTrue(
                    failure.getMessage().contains("cross transactions"));
        }
    }

    @ParameterizedTest
    @EnumSource(ReferenceKind.class)
    void eachReferenceExpiresWhenIssuingTransactionCompletes(
            ReferenceKind kind) {
        try (TransactionFixture ignored =
                     beginTransaction(kind.readOnlyRequired())) {
            Object reference = kind.create(
                    TransactionSynchronizationManager.getResourceMap());
            kind.complete(reference);

            IllegalStateException failure = assertThrows(
                    IllegalStateException.class,
                    () -> kind.consume(reference));

            assertTrue(
                    failure.getMessage().contains("issuing transaction"));
        }
    }

    @ParameterizedTest
    @EnumSource(ReferenceKind.class)
    void eachReferenceIsOpaqueNonSerializableAndRedacted(
            ReferenceKind kind) {
        Object reference = kind.create(Map.of(new Object(), new Object()));

        assertEquals(0, reference.getClass().getConstructors().length);
        assertFalse(reference instanceof Serializable);
        assertTrue(
                java.util.Arrays.stream(
                                reference.getClass().getDeclaredFields())
                        .allMatch(field ->
                                Modifier.isPrivate(field.getModifiers())));
        assertTrue(reference.toString().contains("[REDACTED]"));
        assertFalse(reference.toString().contains("11"));
        assertFalse(reference.toString().contains("22"));
        assertFalse(reference.toString().contains("33"));
        assertFalse(reference.toString().contains(USER_UID.toString()));
    }

    @ParameterizedTest
    @EnumSource(
            value = ReferenceKind.class,
            names = {"DELIVERY_QUERY_USER", "WALLET_QUERY_OWNER"})
    void queryReferencesCannotBeConsumedInAWriteTransaction(
            ReferenceKind kind) {
        try (TransactionFixture ignored = beginTransaction(false)) {
            Object reference = kind.create(
                    TransactionSynchronizationManager.getResourceMap());

            IllegalStateException failure = assertThrows(
                    IllegalStateException.class,
                    () -> kind.consume(reference));

            assertTrue(failure.getMessage().contains("read-only"));
        }
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

    private enum ReferenceKind {
        RECYCLING_SCOPE {
            @Override
            Object create(Map<Object, Object> resources) {
                return new StartDeliveryOrganizationScopeRef(
                        11,
                        22,
                        resources);
            }

            @Override
            long[] consume(Object reference) {
                return ((StartDeliveryOrganizationScopeRef) reference)
                        .withOrganizationScopeOnce(
                                (tenant, organization) ->
                                        new long[] {tenant, organization});
            }

            @Override
            void complete(Object reference) {
                ((StartDeliveryOrganizationScopeRef) reference)
                        .markTransactionCompleted();
            }

            @Override
            long[] expectedKeys() {
                return new long[] {11, 22};
            }
        },
        FUNDS_WALLET {
            @Override
            Object create(Map<Object, Object> resources) {
                return new StartDeliveryWalletOwnerRef(
                        11,
                        22,
                        33,
                        resources);
            }

            @Override
            long[] consume(Object reference) {
                return ((StartDeliveryWalletOwnerRef) reference)
                        .withWalletOwnerOnce(
                                (tenant, organization, user) ->
                                        new long[] {
                                            tenant,
                                            organization,
                                            user
                                        });
            }

            @Override
            void complete(Object reference) {
                ((StartDeliveryWalletOwnerRef) reference)
                        .markTransactionCompleted();
            }

            @Override
            long[] expectedKeys() {
                return new long[] {11, 22, 33};
            }
        },
        DEVICE_SESSION_USER {
            @Override
            Object create(Map<Object, Object> resources) {
                return new DeliverySessionOrganizationUserRef(
                        11,
                        22,
                        33,
                        resources);
            }

            @Override
            long[] consume(Object reference) {
                return ((DeliverySessionOrganizationUserRef) reference)
                        .withDeliverySessionUserOnce(
                                (tenant, organization, user) ->
                                        new long[] {
                                            tenant,
                                            organization,
                                            user
                                        });
            }

            @Override
            void complete(Object reference) {
                ((DeliverySessionOrganizationUserRef) reference)
                        .markTransactionCompleted();
            }

            @Override
            long[] expectedKeys() {
                return new long[] {11, 22, 33};
            }
        },
        DELIVERY_QUERY_USER {
            @Override
            Object create(Map<Object, Object> resources) {
                return new DeliveryQueryOrganizationUserRef(
                        11,
                        22,
                        33,
                        USER_UID,
                        resources);
            }

            @Override
            long[] consume(Object reference) {
                return ((DeliveryQueryOrganizationUserRef) reference)
                        .withDeliveryQueryUserOnce(
                                (tenant, organization, user, userUid) -> {
                                    assertEquals(USER_UID, userUid);
                                    return new long[] {
                                        tenant,
                                        organization,
                                        user
                                    };
                                });
            }

            @Override
            void complete(Object reference) {
                ((DeliveryQueryOrganizationUserRef) reference)
                        .markTransactionCompleted();
            }

            @Override
            long[] expectedKeys() {
                return new long[] {11, 22, 33};
            }

            @Override
            boolean readOnlyRequired() {
                return true;
            }
        },
        WALLET_QUERY_OWNER {
            @Override
            Object create(Map<Object, Object> resources) {
                return new DeliveryWalletQueryOwnerRef(
                        11,
                        22,
                        33,
                        USER_UID,
                        resources);
            }

            @Override
            long[] consume(Object reference) {
                return ((DeliveryWalletQueryOwnerRef) reference)
                        .withWalletQualificationOwnerOnce(
                                (tenant, organization, user, userUid) -> {
                                    assertEquals(USER_UID, userUid);
                                    return new long[] {
                                        tenant,
                                        organization,
                                        user
                                    };
                                });
            }

            @Override
            void complete(Object reference) {
                ((DeliveryWalletQueryOwnerRef) reference)
                        .markTransactionCompleted();
            }

            @Override
            long[] expectedKeys() {
                return new long[] {11, 22, 33};
            }

            @Override
            boolean readOnlyRequired() {
                return true;
            }
        };

        abstract Object create(Map<Object, Object> resources);

        abstract long[] consume(Object reference);

        abstract void complete(Object reference);

        abstract long[] expectedKeys();

        boolean readOnlyRequired() {
            return false;
        }
    }
}
