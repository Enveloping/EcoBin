package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.framework.reliability.PlatformDeviceAssetTaskRef;
import org.enveloping.ecobin.framework.reliability.PlatformDeviceAssetTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistrationPort;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.jdbc.core.JdbcTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDateTime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ReliablePlatformEdgeConfirmationServiceTest {

    private static final String EVENT_UID =
            "10000000-0000-4000-8000-000000000001";

    @Test
    void registersPlatformScopedAcceptanceConfirmation() {
        Fixture fixture = fixture(0);

        fixture.service().ensureApplied(
                13L,
                "test-device-1",
                EVENT_UID,
                "11".repeat(32),
                "UPDATED",
                LocalDateTime.of(2026, 8, 7, 12, 0));

        ArgumentCaptor<ReliablePlatformDeviceControlTaskRegistration> captor =
                ArgumentCaptor.forClass(
                        ReliablePlatformDeviceControlTaskRegistration.class);
        verify(fixture.registrationPort()).register(captor.capture());
        ReliablePlatformDeviceControlTaskRegistration registration =
                captor.getValue();
        assertEquals("CONFIRM_EDGE_EVENT", registration.taskType());
        assertEquals("CONFIRM_EDGE_EVENT:" + EVENT_UID.toUpperCase(),
                registration.taskKey());
        assertEquals(100, registration.maxAutoAttempts());
        JsonNode envelope = new ObjectMapper().readTree(
                registration.executionEnvelope());
        assertEquals("test-device-1",
                envelope.path("targetDeviceName").asText());
        assertEquals("BUSINESS_APPLIED",
                envelope.path("payload").path("outcome").asText());
        assertEquals("UPDATED",
                envelope.path("payload").path("effectKind").asText());
        verify(fixture.taskRefFactory()).issue(13L);
    }

    @Test
    void acceptanceFailureIsReturnedInTheReliableConfirmation() {
        Fixture fixture = fixture(0);

        fixture.service().ensureApplied(
                13L,
                "test-device-1",
                EVENT_UID,
                "11".repeat(32),
                "UPDATED",
                "FAILED",
                LocalDateTime.of(2026, 8, 7, 12, 0));

        ArgumentCaptor<ReliablePlatformDeviceControlTaskRegistration> captor =
                ArgumentCaptor.forClass(
                        ReliablePlatformDeviceControlTaskRegistration.class);
        verify(fixture.registrationPort()).register(captor.capture());
        JsonNode references = new ObjectMapper().readTree(
                captor.getValue().executionEnvelope())
                .path("payload").path("resultReferences");
        assertEquals(1, references.size());
        assertEquals("DEVICE_ACCEPTANCE",
                references.get(0).path("type").asText());
        assertEquals("FAILED",
                references.get(0).path("key").asText());
    }

    @Test
    void existingConfirmationMakesDuplicateEvidenceANoOp() {
        Fixture fixture = fixture(1);

        fixture.service().ensureApplied(
                13L,
                "test-device-1",
                EVENT_UID,
                "11".repeat(32),
                "NO_ACTION_REQUIRED",
                LocalDateTime.of(2026, 8, 7, 12, 0));

        verify(fixture.registrationPort(), never()).register(
                org.mockito.ArgumentMatchers.any());
        verify(fixture.taskRefFactory(), never()).issue(13L);
    }

    private static Fixture fixture(int existingCount) {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(jdbc.queryForObject(
                anyString(), eq(Integer.class), anyString()))
                .thenReturn(existingCount);
        PlatformDeviceAssetTaskRefFactory taskRefFactory =
                mock(PlatformDeviceAssetTaskRefFactory.class);
        when(taskRefFactory.issue(13L))
                .thenReturn(mock(PlatformDeviceAssetTaskRef.class));
        ReliablePlatformDeviceControlTaskRegistrationPort registrationPort =
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class);
        ReliablePlatformEdgeConfirmationService service =
                new ReliablePlatformEdgeConfirmationService(
                        new ObjectMapper(),
                        jdbc,
                        new DeviceConfigurationCanonicalizer(),
                        taskRefFactory,
                        registrationPort);
        return new Fixture(service, taskRefFactory, registrationPort);
    }

    private record Fixture(
            ReliablePlatformEdgeConfirmationService service,
            PlatformDeviceAssetTaskRefFactory taskRefFactory,
            ReliablePlatformDeviceControlTaskRegistrationPort
                    registrationPort) {
    }
}
