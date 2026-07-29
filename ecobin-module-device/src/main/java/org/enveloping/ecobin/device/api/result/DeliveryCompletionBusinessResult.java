package org.enveloping.ecobin.device.api.result;

import java.util.List;

public record DeliveryCompletionBusinessResult(
        String deliveryOrderNo,
        List<DeliveryCompletionResultReference> resultReferences) {

    public DeliveryCompletionBusinessResult {
        if (deliveryOrderNo == null || deliveryOrderNo.isBlank()) {
            throw new IllegalArgumentException(
                    "deliveryOrderNo must not be blank");
        }
        resultReferences = List.copyOf(resultReferences);
    }
}
