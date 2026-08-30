package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.result.DeviceAcceptanceChallengeConsumeResult;

import java.time.LocalDateTime;
import java.util.UUID;

/** Validates and consumes the platform challenge named by trusted evidence. */
public interface TrustedDeviceAcceptanceChallengePort {

    DeviceAcceptanceChallengeConsumeResult consume(
            long assetId,
            UUID commandUid,
            UUID challengeUid,
            long factoryBagRevision,
            byte[] factoryBagSetSha256,
            LocalDateTime receivedAt);

    void cancelOutstanding(long assetId, LocalDateTime cancelledAt);

    /**
     * Cancels only blocked challenges whose frozen command lifetime has
     * elapsed, allowing the automatic coordinator to issue a fresh
     * challenge without replaying an expired command.
     */
    void cancelExpiredBlocked(long assetId, LocalDateTime cancelledAt);
}
