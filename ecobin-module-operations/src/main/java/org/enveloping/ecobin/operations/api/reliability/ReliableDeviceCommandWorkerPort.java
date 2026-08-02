package org.enveloping.ecobin.operations.api.reliability;

/**
 * Public worker Interface used by the external scheduling Adapter.
 */
public interface ReliableDeviceCommandWorkerPort {

    ReliableWorkerBatchResult runBatch(String workerId);

    default int recoverExpiredEvidenceWaits() {
        return 0;
    }
}
