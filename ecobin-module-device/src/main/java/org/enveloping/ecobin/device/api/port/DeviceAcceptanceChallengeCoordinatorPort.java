package org.enveloping.ecobin.device.api.port;

/**
 * Ensures that an online, not-yet-accepted platform asset has an automatic
 * real-hardware acceptance challenge queued.
 */
public interface DeviceAcceptanceChallengeCoordinatorPort {

    boolean requestIfNeeded(long assetId);
}
