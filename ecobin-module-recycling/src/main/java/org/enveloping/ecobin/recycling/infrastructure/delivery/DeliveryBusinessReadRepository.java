package org.enveloping.ecobin.recycling.infrastructure.delivery;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.OptionalLong;
import java.util.Set;

interface DeliveryBusinessReadRepository {

    OptionsRows findCurrentOptions(
            long tenantId,
            long organizationId,
            long assetId,
            List<Long> portIds);

    Optional<String> findDeliveryOrderNo(
            long tenantId,
            long organizationId,
            long deliverySessionId);

    record OptionsRows(
            OptionalLong openBalanceFloorCent,
            Set<Long> portsWithCurrentBag,
            Map<Long, CapacityRow> capacityByPort,
            Set<Long> portsWithActiveBaselineRemeasurement,
            Set<Long> portsWithActiveCleanOperation,
            Set<Long> portsWithCleanRestartInterlock) {
    }

    record CapacityRow(
            String baselineState,
            BigDecimal displayedFullnessPercent,
            String detectionGate,
            String confirmedFullnessState) {
    }
}
