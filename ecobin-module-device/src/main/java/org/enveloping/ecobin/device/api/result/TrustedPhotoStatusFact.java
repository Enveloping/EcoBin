package org.enveloping.ecobin.device.api.result;

import java.time.LocalDateTime;
import java.util.UUID;

/**
 * Photo terminal fact after transport identity, deployment and edge-event
 * identity have been authenticated by the device module.
 */
public record TrustedPhotoStatusFact(
        long tenantId,
        long organizationId,
        long deploymentId,
        long edgeEventId,
        UUID workUid,
        String workType,
        String position,
        String status,
        UUID photoUid,
        String objectUrl,
        byte[] sha256,
        Long sizeBytes,
        LocalDateTime capturedAt,
        String missingReason,
        LocalDateTime deviceOccurredAt,
        LocalDateTime backendReceivedAt) {

    public TrustedPhotoStatusFact {
        sha256 = sha256 == null ? null : sha256.clone();
    }

    @Override
    public byte[] sha256() {
        return sha256 == null ? null : sha256.clone();
    }
}
