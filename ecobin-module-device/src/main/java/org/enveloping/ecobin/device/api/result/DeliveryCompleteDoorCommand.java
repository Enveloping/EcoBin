package org.enveloping.ecobin.device.api.result;

public record DeliveryCompleteDoorCommand(
        String command,
        String outputStatus,
        String physicalStateBasis) {
}
