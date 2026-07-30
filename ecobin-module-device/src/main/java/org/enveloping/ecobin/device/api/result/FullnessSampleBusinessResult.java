package org.enveloping.ecobin.device.api.result;

import java.util.List;

public record FullnessSampleBusinessResult(
        List<DeliveryCompletionResultReference> resultReferences) {

    public FullnessSampleBusinessResult {
        resultReferences = List.copyOf(resultReferences);
    }
}
