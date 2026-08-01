package org.enveloping.ecobin.device.application.deliveryquery;

import org.enveloping.ecobin.device.api.persistence.DeliveryOrderDeviceFactsRef;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

interface DeliveryOrderDeviceQueryRepository {

    List<ResolvedFactRow> findFacts(
            long tenantId,
            long organizationId,
            List<DeliveryOrderDeviceFactsRef.FactKey> facts);

    Optional<DeploymentFilterKeyRow> resolveDeploymentFilter(
            long tenantId,
            long organizationId,
            String deploymentCode,
            Integer portNo);

    List<Long> findPortFilterKeys(
            long tenantId,
            long organizationId,
            int portNo);

    record ResolvedFactRow(
            long deploymentId,
            long portId,
            long deliverySessionId,
            long physicalResultId,
            UUID eventUid,
            UUID sessionUid,
            String deploymentCode,
            int portNo) {
    }

    record DeploymentFilterKeyRow(
            long deploymentId,
            Long portId) {
    }
}
