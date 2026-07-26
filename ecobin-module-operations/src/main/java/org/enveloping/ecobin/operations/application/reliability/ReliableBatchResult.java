package org.enveloping.ecobin.operations.application.reliability;

public record ReliableBatchResult(int claimed, int completed, int failed) {
}
