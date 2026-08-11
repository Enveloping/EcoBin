package org.enveloping.ecobin.device.api.result;

import java.time.LocalDateTime;
import java.util.Objects;
import java.util.UUID;

/** A reliable device command that reached a terminal transport condition. */
public record BlockedDeviceCommand(
        UUID commandUid,
        String commandType,
        Certainty certainty,
        String reasonCode,
        LocalDateTime blockedAt) {

    public BlockedDeviceCommand {
        Objects.requireNonNull(commandUid, "commandUid");
        Objects.requireNonNull(commandType, "commandType");
        Objects.requireNonNull(certainty, "certainty");
        Objects.requireNonNull(reasonCode, "reasonCode");
        Objects.requireNonNull(blockedAt, "blockedAt");
    }

    public enum Certainty {
        DEFINITELY_NOT_ACCEPTED,
        OUTCOME_UNKNOWN
    }
}
