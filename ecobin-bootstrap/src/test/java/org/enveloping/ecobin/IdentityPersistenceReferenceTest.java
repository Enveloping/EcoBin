package org.enveloping.ecobin;

import org.enveloping.ecobin.identity.api.persistence.OrganizationUserWalletOwnerRef;
import org.enveloping.ecobin.identity.application.persistence.OrganizationUserWalletOwnerRefFactory;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;

import java.io.Serializable;
import java.lang.reflect.Modifier;
import java.util.Arrays;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

@SpringBootTest
@ActiveProfiles("test")
class IdentityPersistenceReferenceTest {

    @Autowired
    private OrganizationUserWalletOwnerRefFactory referenceFactory;

    @Autowired
    private PlatformTransactionManager transactionManager;

    @Test
    void referenceIsRedactedNonSerializableSingleUseAndBoundAfterCompletion() {
        AtomicReference<OrganizationUserWalletOwnerRef> captured =
                new AtomicReference<>();
        TransactionTemplate transaction =
                new TransactionTemplate(transactionManager);

        transaction.executeWithoutResult(status -> {
            OrganizationUserWalletOwnerRef reference =
                    referenceFactory.issue(2L, 3L, 17L);
            captured.set(reference);

            long[] foreignKeys = new long[3];
            reference.writeForeignKeyTo((tenant, organization, user) -> {
                foreignKeys[0] = tenant;
                foreignKeys[1] = organization;
                foreignKeys[2] = user;
            });
            assertArrayEquals(new long[]{2L, 3L, 17L}, foreignKeys);

            IllegalStateException repeated = assertThrows(
                    IllegalStateException.class,
                    () -> reference.writeForeignKeyTo(
                            (tenant, organization, user) -> {
                            }));
            assertTrue(repeated.getMessage().contains("already consumed"));
        });

        OrganizationUserWalletOwnerRef reference = captured.get();
        assertNotNull(reference);
        assertEquals(
                "OrganizationUserWalletOwnerRef[REDACTED]",
                reference.toString());
        assertFalse(Serializable.class.isAssignableFrom(
                OrganizationUserWalletOwnerRef.class));
        assertEquals(0, OrganizationUserWalletOwnerRef.class.getConstructors().length);
        assertTrue(Arrays.stream(
                        OrganizationUserWalletOwnerRef.class
                                .getDeclaredMethods())
                .noneMatch(method ->
                        Modifier.isPublic(method.getModifiers())
                                && Modifier.isStatic(method.getModifiers())
                                && method.getReturnType()
                                == OrganizationUserWalletOwnerRef.class));

        IllegalStateException escaped = assertThrows(
                IllegalStateException.class,
                () -> transaction.executeWithoutResult(status ->
                        reference.writeForeignKeyTo(
                                (tenant, organization, user) -> {
                                })));
        assertTrue(escaped.getMessage().contains("issuing transaction"));
        assertSerializationRejectedOrEmpty(reference);
    }

    @Test
    void referenceCannotBeConsumedFromRequiresNew() {
        TransactionTemplate outer = new TransactionTemplate(transactionManager);
        outer.executeWithoutResult(status -> {
            OrganizationUserWalletOwnerRef reference =
                    referenceFactory.issue(2L, 3L, 17L);
            TransactionTemplate requiresNew =
                    new TransactionTemplate(transactionManager);
            requiresNew.setPropagationBehavior(
                    TransactionDefinition.PROPAGATION_REQUIRES_NEW);

            IllegalStateException failure = assertThrows(
                    IllegalStateException.class,
                    () -> requiresNew.executeWithoutResult(inner ->
                            reference.writeForeignKeyTo(
                                    (tenant, organization, user) -> {
                                    })));
            assertTrue(failure.getMessage().contains("cross transactions"));

            reference.writeForeignKeyTo(
                    (tenant, organization, user) -> {
                    });
        });
    }

    @Test
    void referenceCannotCrossThread() {
        TransactionTemplate transaction =
                new TransactionTemplate(transactionManager);
        transaction.executeWithoutResult(status -> {
            OrganizationUserWalletOwnerRef reference =
                    referenceFactory.issue(2L, 3L, 17L);

            CompletionException failure = assertThrows(
                    CompletionException.class,
                    () -> CompletableFuture.runAsync(() ->
                            reference.writeForeignKeyTo(
                                    (tenant, organization, user) -> {
                                    })).join());
            assertTrue(failure.getCause() instanceof IllegalStateException);
            assertTrue(failure.getCause()
                    .getMessage()
                    .contains("cross threads"));

            reference.writeForeignKeyTo(
                    (tenant, organization, user) -> {
                    });
        });
    }

    private static void assertSerializationRejectedOrEmpty(
            OrganizationUserWalletOwnerRef reference) {
        try {
            String json = JsonMapper.builder()
                    .build()
                    .writeValueAsString(reference);
            assertEquals(
                    "{}",
                    json,
                    "a serializer must not expose internal key components");
        } catch (Exception expectedRejection) {
            // Rejecting transport serialization is also a safe outcome.
        }
    }
}
