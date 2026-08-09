package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.framework.reliability.DeviceAssetTaskRef;
import org.enveloping.ecobin.framework.reliability.DeviceAssetTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceControlTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceControlTaskRegistrationPort;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ReliableEdgeConfirmationServiceTest {

    private static final String EVENT_UID =
            "10000000-0000-4000-8000-000000000001";

    @Test
    void limitsReliableEdgeConfirmationToOneHundredAttempts() {
        DeviceConfigurationCanonicalizer canonicalizer =
                mock(DeviceConfigurationCanonicalizer.class);
        when(canonicalizer.payloadSha256(any()))
                .thenReturn(new byte[32]);
        when(canonicalizer.hex(any()))
                .thenReturn("00".repeat(32));

        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(jdbc.query(
                any(String.class),
                any(RowMapper.class),
                anyLong(),
                anyLong(),
                anyLong())).thenReturn(List.of("test-device-1"));
        DeviceAssetTaskRefFactory taskRefFactory =
                mock(DeviceAssetTaskRefFactory.class);
        when(taskRefFactory.issue(anyLong(), anyLong(), anyLong()))
                .thenReturn(mock(DeviceAssetTaskRef.class));
        ReliableDeviceControlTaskRegistrationPort registrationPort =
                mock(ReliableDeviceControlTaskRegistrationPort.class);
        ReliableEdgeConfirmationService service =
                new ReliableEdgeConfirmationService(
                        new ObjectMapper(),
                        jdbc,
                        canonicalizer,
                        taskRefFactory,
                        registrationPort);

        service.registerApplied(
                11L,
                12L,
                13L,
                EVENT_UID,
                "11".repeat(32),
                "DELIVERY_RECORDED",
                LocalDateTime.of(2026, 8, 1, 12, 0));

        ArgumentCaptor<ReliableDeviceControlTaskRegistration> registration =
                ArgumentCaptor.forClass(
                        ReliableDeviceControlTaskRegistration.class);
        verify(registrationPort).register(registration.capture());
        assertEquals(100, registration.getValue().maxAutoAttempts());
    }

    @Test
    void terminalQuarantineConfirmationCarriesNoBusinessEffect()
            throws Exception {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(jdbc.query(
                any(String.class),
                any(RowMapper.class),
                anyLong(),
                anyLong(),
                anyLong())).thenReturn(List.of("test-device-1"));
        DeviceAssetTaskRefFactory taskRefFactory =
                mock(DeviceAssetTaskRefFactory.class);
        when(taskRefFactory.issue(anyLong(), anyLong(), anyLong()))
                .thenReturn(mock(DeviceAssetTaskRef.class));
        ReliableDeviceControlTaskRegistrationPort registrationPort =
                mock(ReliableDeviceControlTaskRegistrationPort.class);
        ReliableEdgeConfirmationService service =
                new ReliableEdgeConfirmationService(
                        new ObjectMapper(),
                        jdbc,
                        new DeviceConfigurationCanonicalizer(),
                        taskRefFactory,
                        registrationPort);
        String digest = "11".repeat(32);
        UUID quarantineUid = UUID.fromString(
                "20000000-0000-4000-8000-000000000002");

        service.registerQuarantined(
                11L,
                12L,
                13L,
                EVENT_UID,
                digest,
                "EVENT_TARGET_NOT_AUTHORITATIVE",
                quarantineUid,
                LocalDateTime.of(2026, 8, 1, 12, 0));

        ArgumentCaptor<ReliableDeviceControlTaskRegistration> captor =
                ArgumentCaptor.forClass(
                        ReliableDeviceControlTaskRegistration.class);
        verify(registrationPort).register(captor.capture());
        ReliableDeviceControlTaskRegistration registration =
                captor.getValue();
        assertEquals(
                "CONFIRM_EDGE_EVENT:" + EVENT_UID.toUpperCase()
                        + ":" + digest.toUpperCase(),
                registration.taskKey());
        JsonNode payload = new ObjectMapper()
                .readTree(registration.executionEnvelope())
                .path("payload");
        assertEquals("EVENT_QUARANTINED",
                payload.path("outcome").asText());
        assertEquals("EVENT_TARGET_NOT_AUTHORITATIVE",
                payload.path("errorCode").asText());
        assertEquals(quarantineUid.toString(),
                payload.path("quarantineUid").asText());
        assertEquals(true, payload.path("effectKind").isNull());
        assertEquals(0, payload.path("resultReferences").size());
    }
}
