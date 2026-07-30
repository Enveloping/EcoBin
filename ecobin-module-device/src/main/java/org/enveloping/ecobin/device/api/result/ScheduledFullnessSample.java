package org.enveloping.ecobin.device.api.result;

import java.util.Objects;
import java.util.UUID;

public record ScheduledFullnessSample(
        UUID commandUid,
        UUID detectionUid,
        String sampleRole) {

    public ScheduledFullnessSample {
        Objects.requireNonNull(commandUid, "commandUid");
        Objects.requireNonNull(detectionUid, "detectionUid");
        Objects.requireNonNull(sampleRole, "sampleRole");
    }
}
