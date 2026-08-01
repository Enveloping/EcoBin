package org.enveloping.ecobin.device.application.deliveryquery;

import org.enveloping.ecobin.device.api.persistence.DeliveryOrderDeviceFactsRef;
import org.enveloping.ecobin.device.api.persistence.DeliveryOrderDeviceFilterRef;
import org.enveloping.ecobin.device.api.query.DeliveryOrderDeviceFilterQuery;
import org.enveloping.ecobin.identity.api.persistence.DeliveryScopePersistenceRef;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.List;
import java.util.Optional;
import java.util.UUID;
import java.util.function.Function;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class DeliveryOrderDeviceQueryServiceTest {

    private static final long TENANT_ID = 11L;
    private static final long ORGANIZATION_ID = 12L;
    private static final UUID EVENT_UID = UUID.fromString(
            "10000000-0000-4000-8000-000000000001");
    private static final UUID SESSION_UID = UUID.fromString(
            "20000000-0000-4000-8000-000000000001");

    @Mock
    private DeliveryOrderDeviceQueryRepository repository;
    @Mock
    private DeliveryReadQueryRefFactory queryRefFactory;
    @Mock
    private DeliveryOrderDeviceFactsRef factsRef;
    @Mock
    private DeliveryScopePersistenceRef scopeRef;
    @Mock
    private DeliveryOrderDeviceFilterRef filterRef;

    private DeliveryOrderDeviceQueryService service;

    @BeforeEach
    void setUp() {
        TransactionSynchronizationManager
                .setActualTransactionActive(true);
        TransactionSynchronizationManager
                .setCurrentTransactionReadOnly(true);
        TransactionSynchronizationManager.initSynchronization();
        service = new DeliveryOrderDeviceQueryService(
                repository,
                queryRefFactory);
    }

    @AfterEach
    void tearDown() {
        if (TransactionSynchronizationManager
                .isSynchronizationActive()) {
            TransactionSynchronizationManager.clearSynchronization();
        }
        TransactionSynchronizationManager
                .setCurrentTransactionReadOnly(false);
        TransactionSynchronizationManager
                .setActualTransactionActive(false);
    }

    @Test
    void factsRemainInCallerOrderAndMismatchedTupleIsFullyMissing() {
        var first = fact("row-1", 101L, 201L, 301L, 401L);
        var second = fact("row-2", 102L, 202L, 302L, 402L);
        var batch = new DeliveryOrderDeviceFactsRef.BatchKeys(
                TENANT_ID,
                ORGANIZATION_ID,
                List.of(first, second));
        answerFactsRef(batch);
        when(repository.findFacts(
                TENANT_ID,
                ORGANIZATION_ID,
                batch.facts())).thenReturn(List.of(
                        new DeliveryOrderDeviceQueryRepository
                                .ResolvedFactRow(
                                102L,
                                999L,
                                302L,
                                402L,
                                EVENT_UID,
                                SESSION_UID,
                                "Dp_wrong_1",
                                6),
                        new DeliveryOrderDeviceQueryRepository
                                .ResolvedFactRow(
                                101L,
                                201L,
                                301L,
                                401L,
                                EVENT_UID,
                                SESSION_UID,
                                "Dp_demo_01",
                                2)));

        var result = service.facts(factsRef);

        assertThat(result).hasSize(2);
        assertThat(result.get(0).token()).isEqualTo("row-1");
        assertThat(result.get(0).resolved()).isTrue();
        assertThat(result.get(0).deploymentCode())
                .isEqualTo("Dp_demo_01");
        assertThat(result.get(1).token()).isEqualTo("row-2");
        assertThat(result.get(1).resolved()).isFalse();
        assertThat(result.get(1).eventUid()).isNull();
        assertThat(result.get(1).sessionUid()).isNull();
        assertThat(result.get(1).deploymentCode()).isNull();
        assertThat(result.get(1).portNo()).isNull();
    }

    @Test
    void factsRejectAReferenceThatInvokesItsCallbackTwice() {
        var batch = new DeliveryOrderDeviceFactsRef.BatchKeys(
                TENANT_ID,
                ORGANIZATION_ID,
                List.of(fact(
                        "row-1",
                        101L,
                        201L,
                        301L,
                        401L)));
        when(factsRef.withFactKeysOnce(any()))
                .thenAnswer(invocation -> {
                    Function<DeliveryOrderDeviceFactsRef.BatchKeys, ?>
                            function = invocation.getArgument(0);
                    function.apply(batch);
                    return function.apply(batch);
                });

        assertThatThrownBy(() -> service.facts(factsRef))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("more than once");
    }

    @Test
    void factsRejectAReferenceThatReturnsWithoutSupplyingKeys() {
        when(factsRef.withFactKeysOnce(any()))
                .thenReturn(List.of());

        assertThatThrownBy(() -> service.facts(factsRef))
                .isInstanceOf(NullPointerException.class)
                .hasMessageContaining("did not supply fact keys");
        verify(repository, never()).findFacts(
                anyLong(),
                anyLong(),
                any());
    }

    @Test
    void filterResolvesInsideIdentityScopeAndReturnsOpaqueKeys() {
        answerScopeRef();
        when(repository.resolveDeploymentFilter(
                TENANT_ID,
                ORGANIZATION_ID,
                "Dp_demo_01",
                2)).thenReturn(Optional.of(
                        new DeliveryOrderDeviceQueryRepository
                                .DeploymentFilterKeyRow(101L, 201L)));
        when(queryRefFactory.issueOrderFilter(
                TENANT_ID,
                ORGANIZATION_ID,
                101L,
                List.of(201L))).thenReturn(filterRef);

        var result = service.resolveFilter(
                new DeliveryOrderDeviceFilterQuery(
                        "Dp_demo_01",
                        2,
                        scopeRef));

        assertThat(result).containsSame(filterRef);
        verify(repository).resolveDeploymentFilter(
                TENANT_ID,
                ORGANIZATION_ID,
                "Dp_demo_01",
                2);
    }

    @Test
    void missingRequestedPortReturnsEmptyWithoutIssuingRawKeys() {
        answerScopeRef();
        when(repository.resolveDeploymentFilter(
                TENANT_ID,
                ORGANIZATION_ID,
                "Dp_demo_01",
                2)).thenReturn(Optional.of(
                        new DeliveryOrderDeviceQueryRepository
                                .DeploymentFilterKeyRow(101L, null)));

        var result = service.resolveFilter(
                new DeliveryOrderDeviceFilterQuery(
                        "Dp_demo_01",
                        2,
                        scopeRef));

        assertThat(result).isEmpty();
        verify(queryRefFactory, never())
                .issueOrderFilter(anyLong(),
                        anyLong(),
                        any(),
                        any());
    }

    @Test
    void deploymentOnlyFilterIssuesAnEmptyPortKeySet() {
        answerScopeRef();
        when(repository.resolveDeploymentFilter(
                TENANT_ID,
                ORGANIZATION_ID,
                "Dp_demo_01",
                null)).thenReturn(Optional.of(
                        new DeliveryOrderDeviceQueryRepository
                                .DeploymentFilterKeyRow(101L, null)));
        when(queryRefFactory.issueOrderFilter(
                TENANT_ID,
                ORGANIZATION_ID,
                101L,
                List.of())).thenReturn(filterRef);

        var result = service.resolveFilter(
                new DeliveryOrderDeviceFilterQuery(
                        "Dp_demo_01",
                        null,
                        scopeRef));

        assertThat(result).containsSame(filterRef);
    }

    @Test
    void portOnlyFilterResolvesEveryMatchingPortInTheScope() {
        answerScopeRef();
        when(repository.findPortFilterKeys(
                TENANT_ID,
                ORGANIZATION_ID,
                2)).thenReturn(List.of(201L, 202L));
        when(queryRefFactory.issueOrderFilter(
                TENANT_ID,
                ORGANIZATION_ID,
                null,
                List.of(201L, 202L))).thenReturn(filterRef);

        var result = service.resolveFilter(
                new DeliveryOrderDeviceFilterQuery(
                        null,
                        2,
                        scopeRef));

        assertThat(result).containsSame(filterRef);
        verify(repository).findPortFilterKeys(
                TENANT_ID,
                ORGANIZATION_ID,
                2);
    }

    @Test
    void invalidDeploymentCodeStillConsumesScopeButDoesNotQuery() {
        answerScopeRef();

        var result = service.resolveFilter(
                new DeliveryOrderDeviceFilterQuery(
                        "not-a-deployment",
                        null,
                        scopeRef));

        assertThat(result).isEmpty();
        verify(scopeRef).withScopeOnce(any());
        verify(repository, never()).resolveDeploymentFilter(
                anyLong(),
                anyLong(),
                any(),
                any());
    }

    @Test
    void allQueriesRequireAnExistingReadOnlyTransaction() {
        TransactionSynchronizationManager
                .setCurrentTransactionReadOnly(false);

        assertThatThrownBy(() -> service.facts(factsRef))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("read-only transaction");
    }

    @Test
    void filterRequiresAtLeastOnePublicDeviceCriterion() {
        assertThatThrownBy(() ->
                new DeliveryOrderDeviceFilterQuery(
                        null,
                        null,
                        scopeRef))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("required");
    }

    private void answerFactsRef(
            DeliveryOrderDeviceFactsRef.BatchKeys batch) {
        when(factsRef.withFactKeysOnce(any()))
                .thenAnswer(invocation -> {
                    Function<DeliveryOrderDeviceFactsRef.BatchKeys, ?>
                            function = invocation.getArgument(0);
                    return function.apply(batch);
                });
    }

    private void answerScopeRef() {
        when(scopeRef.withScopeOnce(any()))
                .thenAnswer(invocation -> {
                    DeliveryScopePersistenceRef.ScopeFunction<?>
                            function = invocation.getArgument(0);
                    return function.apply(
                            TENANT_ID,
                            ORGANIZATION_ID,
                            501L,
                            null);
                });
    }

    private static DeliveryOrderDeviceFactsRef.FactKey fact(
            String token,
            long deploymentId,
            long portId,
            long sessionId,
            long resultId) {
        return new DeliveryOrderDeviceFactsRef.FactKey(
                token,
                deploymentId,
                portId,
                sessionId,
                resultId);
    }
}
