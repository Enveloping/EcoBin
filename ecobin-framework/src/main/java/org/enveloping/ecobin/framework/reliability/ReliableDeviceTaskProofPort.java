package org.enveloping.ecobin.framework.reliability;

/**
 * Mutation Seam used by a trusted device fact to terminate its reliable
 * command task in the same authoritative business transaction.
 */
public interface ReliableDeviceTaskProofPort {

    void completeFromTrustedProof(
            String taskType,
            String targetType,
            String targetStableKey);
}
