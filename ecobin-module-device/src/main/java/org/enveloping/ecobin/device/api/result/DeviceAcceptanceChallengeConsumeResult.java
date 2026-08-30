package org.enveloping.ecobin.device.api.result;

/**
 * Result of resolving the exact acceptance challenge named by trusted
 * device evidence.
 */
public enum DeviceAcceptanceChallengeConsumeResult {
    /** The challenge is active or was already consumed; evaluate evidence. */
    CONSUMED,

    /** The exact challenge was cancelled; acknowledge without evaluation. */
    CANCELLED
}
