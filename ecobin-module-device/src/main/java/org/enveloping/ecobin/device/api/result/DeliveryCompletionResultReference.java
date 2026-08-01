package org.enveloping.ecobin.device.api.result;

public record DeliveryCompletionResultReference(
        String type,
        String key) {

    public DeliveryCompletionResultReference {
        if (type == null || !type.matches("[A-Z][A-Z0-9_]{0,63}")) {
            throw new IllegalArgumentException(
                    "type must be a stable code");
        }
        if (key == null || key.isBlank()) {
            throw new IllegalArgumentException(
                    "key must not be blank");
        }
    }
}
