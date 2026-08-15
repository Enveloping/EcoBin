package org.enveloping.ecobin.device.api.port;

import java.time.LocalDateTime;
import java.util.UUID;

/** Validates and consumes the platform challenge named by trusted evidence. */
public interface TrustedDeviceAcceptanceChallengePort {

    void consume(
            long assetId,
            UUID commandUid,
            UUID challengeUid,
            long factoryBagRevision,
            byte[] factoryBagSetSha256,
            LocalDateTime receivedAt);

    void cancelOutstanding(long assetId, LocalDateTime cancelledAt);
}
