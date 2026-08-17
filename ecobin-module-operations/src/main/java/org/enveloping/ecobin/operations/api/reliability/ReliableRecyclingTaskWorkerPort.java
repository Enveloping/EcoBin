package org.enveloping.ecobin.operations.api.reliability;

/** Scheduling adapter 使用的回收业务可靠任务边界。 */
public interface ReliableRecyclingTaskWorkerPort {

    boolean runNext(String workerId);
}
