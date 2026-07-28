package org.enveloping.ecobin.operations.api.reliability;

public record ReliableWorkerBatchResult(
        int claimed,
        int accepted,
        int failed) {
}
