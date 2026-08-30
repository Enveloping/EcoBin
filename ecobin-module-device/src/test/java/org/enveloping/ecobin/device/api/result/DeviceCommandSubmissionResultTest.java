package org.enveloping.ecobin.device.api.result;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class DeviceCommandSubmissionResultTest {

    @Test
    void keepsOnlyBoundedSafeAsciiExternalRequestIdentity() {
        DeviceCommandSubmissionResult result =
                new DeviceCommandSubmissionResult(
                        DeviceCommandSubmissionResult.Outcome
                                .PERMANENT_FAILURE,
                        null,
                        null,
                        200,
                        "ONENET_10415",
                        "a25087f46df04b69b29e90ef0acfd115",
                        "required value");

        assertEquals(
                "a25087f46df04b69b29e90ef0acfd115",
                result.externalRequestId());
        assertThrows(
                IllegalArgumentException.class,
                () -> new DeviceCommandSubmissionResult(
                        DeviceCommandSubmissionResult.Outcome
                                .PERMANENT_FAILURE,
                        null,
                        null,
                        200,
                        "ONENET_10415",
                        "request id contains spaces",
                        "required value"));
        assertThrows(
                IllegalArgumentException.class,
                () -> new DeviceCommandSubmissionResult(
                        DeviceCommandSubmissionResult.Outcome
                                .PERMANENT_FAILURE,
                        null,
                        null,
                        200,
                        "ONENET_10415",
                        "a".repeat(129),
                        "required value"));
    }
}
