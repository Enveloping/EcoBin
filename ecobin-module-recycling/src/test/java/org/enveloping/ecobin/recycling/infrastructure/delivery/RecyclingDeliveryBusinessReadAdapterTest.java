package org.enveloping.ecobin.recycling.infrastructure.delivery;

import org.enveloping.ecobin.device.api.persistence.DeliveryOptionsBusinessQueryRef;
import org.enveloping.ecobin.device.api.persistence.DeliverySessionBusinessQueryRef;
import org.enveloping.ecobin.recycling.application.deliveryquery.DeliveryPortBusinessFacts;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.OptionalLong;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class RecyclingDeliveryBusinessReadAdapterTest {

    @BeforeEach
    void beginReadOnlyTransaction() {
        TransactionSynchronizationManager.setActualTransactionActive(true);
        TransactionSynchronizationManager
                .setCurrentTransactionReadOnly(true);
    }

    @AfterEach
    void endReadOnlyTransaction() {
        TransactionSynchronizationManager
                .setCurrentTransactionReadOnly(false);
        TransactionSynchronizationManager
                .setActualTransactionActive(false);
    }

    @Test
    void convertsRecyclingRowsForEveryDeviceIssuedPort() {
        FakeRepository repository = new FakeRepository();
        repository.optionsRows =
                new DeliveryBusinessReadRepository.OptionsRows(
                        OptionalLong.of(-100),
                        Set.of(101L),
                        Map.of(
                                101L,
                                new DeliveryBusinessReadRepository.CapacityRow(
                                        "VALID",
                                        new BigDecimal("42.50"),
                                        "READY",
                                        "NOT_FULL")),
                        Set.of(102L),
                        Set.of(102L));
        RecyclingDeliveryBusinessReadAdapter adapter =
                new RecyclingDeliveryBusinessReadAdapter(repository);

        var result = adapter.currentOptions(optionsReference(
                11,
                22,
                33,
                List.of(
                        new DeliveryOptionsBusinessQueryRef.PortKey(101, 1),
                        new DeliveryOptionsBusinessQueryRef.PortKey(102, 2))));

        assertThat(result.openBalanceFloorCent().orElseThrow())
                .isEqualTo(-100);
        assertThat(result.ports()).hasSize(2);

        DeliveryPortBusinessFacts first = result.port(1).orElseThrow();
        assertThat(first.currentBagPresent()).isTrue();
        assertThat(first.baselineState())
                .isEqualTo(DeliveryPortBusinessFacts.BaselineState.VALID);
        assertThat(first.displayedFullnessPercent())
                .isEqualByComparingTo("42.50");
        assertThat(first.detectionGate())
                .isEqualTo(DeliveryPortBusinessFacts.DetectionGate.READY);
        assertThat(first.confirmedFullnessState()).isEqualTo(
                DeliveryPortBusinessFacts
                        .ConfirmedFullnessState.NOT_FULL);
        assertThat(first.baselineRemeasurementActive()).isFalse();
        assertThat(first.cleanOperationActive()).isFalse();

        DeliveryPortBusinessFacts second = result.port(2).orElseThrow();
        assertThat(second.currentBagPresent()).isFalse();
        assertThat(second.baselineState())
                .isEqualTo(DeliveryPortBusinessFacts.BaselineState.MISSING);
        assertThat(second.displayedFullnessPercent()).isNull();
        assertThat(second.detectionGate())
                .isEqualTo(DeliveryPortBusinessFacts.DetectionGate.MISSING);
        assertThat(second.baselineRemeasurementActive()).isTrue();
        assertThat(second.cleanOperationActive()).isTrue();

        assertThat(repository.tenantId).isEqualTo(11);
        assertThat(repository.organizationId).isEqualTo(22);
        assertThat(repository.deploymentId).isEqualTo(33);
        assertThat(repository.portIds).containsExactly(101L, 102L);
    }

    @Test
    void keepsMissingCurrentConfigurationExplicit() {
        FakeRepository repository = new FakeRepository();
        repository.optionsRows =
                new DeliveryBusinessReadRepository.OptionsRows(
                        OptionalLong.empty(),
                        Set.of(),
                        Map.of(),
                        Set.of(),
                        Set.of());
        RecyclingDeliveryBusinessReadAdapter adapter =
                new RecyclingDeliveryBusinessReadAdapter(repository);

        var result = adapter.currentOptions(optionsReference(
                11,
                22,
                33,
                List.of(
                        new DeliveryOptionsBusinessQueryRef.PortKey(101, 1))));

        assertThat(result.deliveryConfigurationPresent()).isFalse();
        assertThat(result.openBalanceFloorCent()).isEmpty();
    }

    @Test
    void resolvesAnOrderOnlyByTheDeviceIssuedSessionKey() {
        FakeRepository repository = new FakeRepository();
        repository.deliveryOrderNo = Optional.of("DO-00000001");
        RecyclingDeliveryBusinessReadAdapter adapter =
                new RecyclingDeliveryBusinessReadAdapter(repository);

        Optional<String> result = adapter.findDeliveryOrderNo(
                sessionReference(11, 22, 44));

        assertThat(result).contains("DO-00000001");
        assertThat(repository.tenantId).isEqualTo(11);
        assertThat(repository.organizationId).isEqualTo(22);
        assertThat(repository.deliverySessionId).isEqualTo(44);
    }

    @Test
    void methodsRequireAnExistingReadOnlyOuterTransaction()
            throws NoSuchMethodException {
        Transactional optionsAnnotation =
                RecyclingDeliveryBusinessReadAdapter.class
                        .getMethod(
                                "currentOptions",
                                DeliveryOptionsBusinessQueryRef.class)
                        .getAnnotation(Transactional.class);
        Transactional sessionAnnotation =
                RecyclingDeliveryBusinessReadAdapter.class
                        .getMethod(
                                "findDeliveryOrderNo",
                                DeliverySessionBusinessQueryRef.class)
                        .getAnnotation(Transactional.class);

        assertThat(optionsAnnotation.propagation())
                .isEqualTo(Propagation.MANDATORY);
        assertThat(optionsAnnotation.readOnly()).isTrue();
        assertThat(sessionAnnotation.propagation())
                .isEqualTo(Propagation.MANDATORY);
        assertThat(sessionAnnotation.readOnly()).isTrue();

        TransactionSynchronizationManager
                .setCurrentTransactionReadOnly(false);
        assertThrows(
                IllegalStateException.class,
                () -> new RecyclingDeliveryBusinessReadAdapter(
                        new FakeRepository())
                        .currentOptions(mock(
                                DeliveryOptionsBusinessQueryRef.class)));
    }

    @SuppressWarnings("unchecked")
    private static DeliveryOptionsBusinessQueryRef optionsReference(
            long tenantId,
            long organizationId,
            long deploymentId,
            List<DeliveryOptionsBusinessQueryRef.PortKey> ports) {
        DeliveryOptionsBusinessQueryRef reference =
                mock(DeliveryOptionsBusinessQueryRef.class);
        when(reference.withOptionsBusinessKeysOnce(any()))
                .thenAnswer(invocation -> {
                    DeliveryOptionsBusinessQueryRef
                            .OptionsBusinessFunction<Object>
                            function = invocation.getArgument(0);
                    return function.apply(
                            tenantId,
                            organizationId,
                            deploymentId,
                            ports);
                });
        return reference;
    }

    @SuppressWarnings("unchecked")
    private static DeliverySessionBusinessQueryRef sessionReference(
            long tenantId,
            long organizationId,
            long deliverySessionId) {
        DeliverySessionBusinessQueryRef reference =
                mock(DeliverySessionBusinessQueryRef.class);
        when(reference.withSessionBusinessKeysOnce(any()))
                .thenAnswer(invocation -> {
                    DeliverySessionBusinessQueryRef
                            .SessionBusinessFunction<Object>
                            function = invocation.getArgument(0);
                    return function.apply(
                            tenantId,
                            organizationId,
                            deliverySessionId);
                });
        return reference;
    }

    private static final class FakeRepository
            implements DeliveryBusinessReadRepository {

        private OptionsRows optionsRows = new OptionsRows(
                OptionalLong.empty(),
                Set.of(),
                Map.of(),
                Set.of(),
                Set.of());
        private Optional<String> deliveryOrderNo = Optional.empty();
        private long tenantId;
        private long organizationId;
        private long deploymentId;
        private long deliverySessionId;
        private List<Long> portIds = List.of();

        @Override
        public OptionsRows findCurrentOptions(
                long tenantId,
                long organizationId,
                long deploymentId,
                List<Long> portIds) {
            this.tenantId = tenantId;
            this.organizationId = organizationId;
            this.deploymentId = deploymentId;
            this.portIds = List.copyOf(portIds);
            return optionsRows;
        }

        @Override
        public Optional<String> findDeliveryOrderNo(
                long tenantId,
                long organizationId,
                long deliverySessionId) {
            this.tenantId = tenantId;
            this.organizationId = organizationId;
            this.deliverySessionId = deliverySessionId;
            return deliveryOrderNo;
        }
    }
}
