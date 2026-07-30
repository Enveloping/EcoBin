package org.enveloping.ecobin.device.api.result;

import java.util.List;
import java.util.UUID;

public record TrustedPhotoGrantWork(
        long tenantId,
        long organizationId,
        long deploymentId,
        String workType,
        UUID workUid,
        List<String> requestedSlots) {

    public TrustedPhotoGrantWork {
        requestedSlots = List.copyOf(requestedSlots);
    }
}
