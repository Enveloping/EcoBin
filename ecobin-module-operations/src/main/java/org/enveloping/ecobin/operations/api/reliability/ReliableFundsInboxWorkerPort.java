package org.enveloping.ecobin.operations.api.reliability;

public interface ReliableFundsInboxWorkerPort {

    ReliableWorkerBatchResult runBatch(String workerId);
}
