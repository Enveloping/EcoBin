package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.result.DeliveryCompletionResultReference;

import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

/**
 * Registers the durable business confirmation for one trusted edge fact.
 */
public interface ReliableEdgeConfirmationPort {

    UUID registerApplied(
            long tenantId,
            long organizationId,
            long deploymentId,
            String deploymentCode,
            String originalEventUid,
            String originalPayloadSha256,
            String effectKind,
            List<DeliveryCompletionResultReference> resultReferences,
            LocalDateTime processedAt);
}
