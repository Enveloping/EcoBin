package org.enveloping.ecobin.device.api.persistence;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class DeliveryReadQueryReferenceTest {

    private final Object resourceKey = new Object();
    private final Object resourceValue = new Object();

    @BeforeEach
    void setUp() {
        TransactionSynchronizationManager
                .setActualTransactionActive(true);
        TransactionSynchronizationManager
                .setCurrentTransactionReadOnly(true);
        TransactionSynchronizationManager
                .bindResource(resourceKey, resourceValue);
    }

    @AfterEach
    void tearDown() {
        if (TransactionSynchronizationManager
                .hasResource(resourceKey)) {
            TransactionSynchronizationManager
                    .unbindResource(resourceKey);
        }
        TransactionSynchronizationManager
                .setCurrentTransactionReadOnly(false);
        TransactionSynchronizationManager
                .setActualTransactionActive(false);
    }

    @Test
    void optionsReferenceIsSameTransactionOnceOnlyAndRedacted() {
        DeliveryOptionsBusinessQueryRef reference =
                new DeliveryOptionsBusinessQueryRef(
                        11L,
                        12L,
                        101L,
                        List.of(
                                new DeliveryOptionsBusinessQueryRef
                                        .PortKey(201L, 2)),
                        TransactionSynchronizationManager
                                .getResourceMap());

        String result = reference.withOptionsBusinessKeysOnce(
                (tenantId, organizationId, deploymentId, ports) ->
                        tenantId + ":"
                                + organizationId + ":"
                                + deploymentId + ":"
                                + ports.getFirst().portNo());

        assertThat(result).isEqualTo("11:12:101:2");
        assertThat(reference.toString())
                .isEqualTo(
                        "DeliveryOptionsBusinessQueryRef[REDACTED]")
                .doesNotContain("11", "12", "101", "201");
        assertThatThrownBy(() ->
                reference.withOptionsBusinessKeysOnce(
                        (tenantId,
                         organizationId,
                         deploymentId,
                         ports) -> null))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("already consumed");
    }

    @Test
    void sessionReferenceExpiresWithIssuingTransaction() {
        DeliverySessionBusinessQueryRef reference =
                new DeliverySessionBusinessQueryRef(
                        11L,
                        12L,
                        301L,
                        TransactionSynchronizationManager
                                .getResourceMap());
        reference.markTransactionCompleted();

        assertThatThrownBy(() ->
                reference.withSessionBusinessKeysOnce(
                        (tenantId,
                         organizationId,
                         sessionId) -> sessionId))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("issuing read-only transaction");
        assertThat(reference.toString())
                .isEqualTo(
                        "DeliverySessionBusinessQueryRef[REDACTED]")
                .doesNotContain("11", "12", "301");
    }

    @Test
    void referenceRejectsAnotherBoundTransactionResource() {
        DeliverySessionBusinessQueryRef reference =
                new DeliverySessionBusinessQueryRef(
                        11L,
                        12L,
                        301L,
                        TransactionSynchronizationManager
                                .getResourceMap());
        TransactionSynchronizationManager
                .unbindResource(resourceKey);
        TransactionSynchronizationManager.bindResource(
                resourceKey,
                new Object());

        assertThatThrownBy(() ->
                reference.withSessionBusinessKeysOnce(
                        (tenantId,
                         organizationId,
                         sessionId) -> sessionId))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("cross transactions");
    }
}
