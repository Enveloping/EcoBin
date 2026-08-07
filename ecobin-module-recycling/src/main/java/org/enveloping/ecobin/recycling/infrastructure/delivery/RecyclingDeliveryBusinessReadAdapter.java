package org.enveloping.ecobin.recycling.infrastructure.delivery;

import org.enveloping.ecobin.device.api.persistence.DeliveryOptionsBusinessQueryRef;
import org.enveloping.ecobin.device.api.persistence.DeliverySessionBusinessQueryRef;
import org.enveloping.ecobin.recycling.application.deliveryquery.DeliveryBusinessReadPort;
import org.enveloping.ecobin.recycling.application.deliveryquery.DeliveryOptionsBusinessFacts;
import org.enveloping.ecobin.recycling.application.deliveryquery.DeliveryPortBusinessFacts;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.List;
import java.util.Objects;
import java.util.Optional;

/**
 * Consumes device-issued, transaction-bound read references and projects only
 * recycling-owned facts.
 */
@Component
public class RecyclingDeliveryBusinessReadAdapter
        implements DeliveryBusinessReadPort {

    private final DeliveryBusinessReadRepository repository;

    RecyclingDeliveryBusinessReadAdapter(
            DeliveryBusinessReadRepository repository) {
        this.repository = repository;
    }

    @Override
    @Transactional(
            propagation = Propagation.MANDATORY,
            readOnly = true)
    public DeliveryOptionsBusinessFacts currentOptions(
            DeliveryOptionsBusinessQueryRef queryRef) {
        requireReadOnlyTransaction();
        Objects.requireNonNull(queryRef, "queryRef");
        return queryRef.withOptionsBusinessKeysOnce(
                (tenantId,
                        organizationId,
                        assetId,
                        ports) -> currentOptionsWithinScope(
                                tenantId,
                                organizationId,
                                assetId,
                                ports));
    }

    @Override
    @Transactional(
            propagation = Propagation.MANDATORY,
            readOnly = true)
    public Optional<String> findDeliveryOrderNo(
            DeliverySessionBusinessQueryRef queryRef) {
        requireReadOnlyTransaction();
        Objects.requireNonNull(queryRef, "queryRef");
        return queryRef.withSessionBusinessKeysOnce(
                repository::findDeliveryOrderNo);
    }

    private DeliveryOptionsBusinessFacts currentOptionsWithinScope(
            long tenantId,
            long organizationId,
            long assetId,
            List<DeliveryOptionsBusinessQueryRef.PortKey> ports) {
        List<Long> portIds = ports.stream()
                .map(DeliveryOptionsBusinessQueryRef.PortKey::portKey)
                .toList();
        DeliveryBusinessReadRepository.OptionsRows rows =
                repository.findCurrentOptions(
                        tenantId,
                        organizationId,
                        assetId,
                        portIds);
        List<DeliveryPortBusinessFacts> portFacts = ports.stream()
                .map(port -> toPortFacts(port, rows))
                .toList();
        return new DeliveryOptionsBusinessFacts(
                rows.openBalanceFloorCent(),
                portFacts);
    }

    private static DeliveryPortBusinessFacts toPortFacts(
            DeliveryOptionsBusinessQueryRef.PortKey port,
            DeliveryBusinessReadRepository.OptionsRows rows) {
        long portId = port.portKey();
        DeliveryBusinessReadRepository.CapacityRow capacity =
                rows.capacityByPort().get(portId);
        if (capacity == null) {
            return new DeliveryPortBusinessFacts(
                    port.portNo(),
                    rows.portsWithCurrentBag().contains(portId),
                    DeliveryPortBusinessFacts.BaselineState.MISSING,
                    null,
                    DeliveryPortBusinessFacts.DetectionGate.MISSING,
                    DeliveryPortBusinessFacts
                            .ConfirmedFullnessState.MISSING,
                    rows.portsWithActiveBaselineRemeasurement()
                            .contains(portId),
                    rows.portsWithActiveCleanOperation()
                            .contains(portId),
                    rows.portsWithCleanRestartInterlock()
                            .contains(portId));
        }
        return new DeliveryPortBusinessFacts(
                port.portNo(),
                rows.portsWithCurrentBag().contains(portId),
                DeliveryPortBusinessFacts.BaselineState.valueOf(
                        capacity.baselineState()),
                capacity.displayedFullnessPercent(),
                DeliveryPortBusinessFacts.DetectionGate.valueOf(
                        capacity.detectionGate()),
                DeliveryPortBusinessFacts.ConfirmedFullnessState.valueOf(
                        capacity.confirmedFullnessState()),
                rows.portsWithActiveBaselineRemeasurement()
                        .contains(portId),
                rows.portsWithActiveCleanOperation().contains(portId),
                rows.portsWithCleanRestartInterlock().contains(portId));
    }

    private static void requireReadOnlyTransaction() {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()
                || !TransactionSynchronizationManager
                .isCurrentTransactionReadOnly()) {
            throw new IllegalStateException(
                    "delivery business query requires an existing "
                            + "read-only transaction");
        }
    }
}
