package org.enveloping.ecobin.device.api.port;

import java.time.LocalDateTime;
import java.util.UUID;

/** Narrow operations boundary for one platform-scoped factory-seal task. */
public interface FactorySealReliableTaskPort {

    void completeAcceptedCommand(UUID commandUid, LocalDateTime completedAt);

    void cancelTask(
            UUID taskUid,
            String reasonCode,
            LocalDateTime cancelledAt);
}
