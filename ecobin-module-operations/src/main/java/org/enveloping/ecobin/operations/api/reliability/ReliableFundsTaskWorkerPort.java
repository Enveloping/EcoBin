package org.enveloping.ecobin.operations.api.reliability;

/** Public funds task worker boundary used by the scheduling adapter. */
public interface ReliableFundsTaskWorkerPort {

    boolean runNext(String workerId);
}
