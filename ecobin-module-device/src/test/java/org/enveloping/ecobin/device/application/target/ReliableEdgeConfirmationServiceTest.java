package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.framework.reliability.DeviceDeploymentTaskRef;
import org.enveloping.ecobin.framework.reliability.DeviceDeploymentTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceControlTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceControlTaskRegistrationPort;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDateTime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ReliableEdgeConfirmationServiceTest {

    @Test
    void limitsReliableEdgeConfirmationToOneHundredAttempts() {
        DeviceConfigurationCanonicalizer canonicalizer =
                mock(DeviceConfigurationCanonicalizer.class);
        when(canonicalizer.payloadSha256(any()))
                .thenReturn(new byte[32]);
        when(canonicalizer.hex(any()))
                .thenReturn("00".repeat(32));

        DeviceDeploymentTaskRefFactory taskRefFactory =
                mock(DeviceDeploymentTaskRefFactory.class);
        when(taskRefFactory.issue(anyLong(), anyLong(), anyLong()))
                .thenReturn(mock(DeviceDeploymentTaskRef.class));
        ReliableDeviceControlTaskRegistrationPort registrationPort =
                mock(ReliableDeviceControlTaskRegistrationPort.class);
        ReliableEdgeConfirmationService service =
                new ReliableEdgeConfirmationService(
                        new ObjectMapper(),
                        canonicalizer,
                        taskRefFactory,
                        registrationPort);

        service.registerApplied(
                11L,
                12L,
                13L,
                "deployment-13",
                "10000000-0000-4000-8000-000000000001",
                "11".repeat(32),
                "DELIVERY_RECORDED",
                LocalDateTime.of(2026, 8, 1, 12, 0));

        ArgumentCaptor<ReliableDeviceControlTaskRegistration> registration =
                ArgumentCaptor.forClass(
                        ReliableDeviceControlTaskRegistration.class);
        verify(registrationPort).register(registration.capture());
        assertEquals(100, registration.getValue().maxAutoAttempts());
    }
}
