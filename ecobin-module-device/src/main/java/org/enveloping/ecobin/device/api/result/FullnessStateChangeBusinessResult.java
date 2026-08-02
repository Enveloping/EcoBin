package org.enveloping.ecobin.device.api.result;

import java.util.List;

public record FullnessStateChangeBusinessResult(
        List<DeliveryCompletionResultReference> resultReferences) {

    public FullnessStateChangeBusinessResult {
        resultReferences = List.copyOf(resultReferences);
    }
}
