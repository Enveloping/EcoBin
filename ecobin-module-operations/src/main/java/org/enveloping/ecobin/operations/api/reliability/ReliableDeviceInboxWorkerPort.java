package org.enveloping.ecobin.operations.api.reliability;

/**
 * Public worker Interface for durable device inbox processing.
 */
public interface ReliableDeviceInboxWorkerPort {

    ReliableWorkerBatchResult runBatch(String workerId);
}
