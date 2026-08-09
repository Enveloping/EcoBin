package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskProofPort;
import org.enveloping.ecobin.framework.reliability.TrustedInboxQuarantinePort;
import org.enveloping.ecobin.framework.reliability.TrustedOrganizationInboxRef;
import org.enveloping.ecobin.framework.reliability.TrustedOrganizationInboxRefFactory;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.contains;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class TrustedConfigurationProgressServiceTest {

    @Test
    @SuppressWarnings("unchecked")
    void obsoleteAuthoritativeTargetIsQuarantinedAndConfirmed() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(jdbc.query(
                contains("FROM dev_device_asset"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenReturn(List.of(41L));
        when(jdbc.query(
                contains("FROM dev_config_application"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenReturn(List.of());
        when(jdbc.query(
                contains("FROM dev_device_command"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenReturn(List.of());
        LocalDateTime now = LocalDateTime.of(2026, 8, 9, 12, 0);
        when(jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class))
                .thenReturn(now);

        ReliableDeviceTaskProofPort proofPort =
                mock(ReliableDeviceTaskProofPort.class);
        ReliableEdgeConfirmationService confirmationService =
                mock(ReliableEdgeConfirmationService.class);
        TrustedInboxQuarantinePort quarantinePort =
                mock(TrustedInboxQuarantinePort.class);
        TrustedOrganizationInboxRefFactory inboxRefFactory =
                mock(TrustedOrganizationInboxRefFactory.class);
        TrustedOrganizationInboxRef sourceInbox =
                mock(TrustedOrganizationInboxRef.class);
        TrustedOrganizationInboxRef quarantineInbox =
                mock(TrustedOrganizationInboxRef.class);
        when(sourceInbox.use(any())).thenAnswer(invocation -> {
            TrustedOrganizationInboxRef.ScopedInboxFunction<Object> function =
                    invocation.getArgument(0);
            return function.apply(7L, 11L, 13L);
        });
        when(inboxRefFactory.issue(7L, 11L, 13L))
                .thenReturn(quarantineInbox);
        UUID quarantineUid =
                UUID.fromString("20000000-0000-4000-8000-000000000001");
        when(quarantinePort.quarantine(
                eq(quarantineInbox),
                eq("EVENT_TARGET_NOT_AUTHORITATIVE"),
                anyString()))
                .thenReturn(quarantineUid);

        TrustedConfigurationProgressService service =
                new TrustedConfigurationProgressService(
                        jdbc,
                        new ObjectMapper(),
                        proofPort,
                        confirmationService,
                        quarantinePort,
                        inboxRefFactory,
                        mock(AutomaticDeviceActivationService.class));

        TrustedDeviceEventApplyResult result = service.apply(
                new TrustedDeviceInboxEvent(
                        sourceInbox,
                        "CONFIGURATION_PROGRESS",
                        2,
                        obsoleteProgressPayload()));

        assertEquals(TrustedDeviceEventApplyResult.QUARANTINED, result);
        verify(quarantinePort).quarantine(
                eq(quarantineInbox),
                eq("EVENT_TARGET_NOT_AUTHORITATIVE"),
                anyString());
        verify(confirmationService).registerQuarantined(
                11L,
                13L,
                41L,
                "10000000-0000-4000-8000-000000000001",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "EVENT_TARGET_NOT_AUTHORITATIVE",
                quarantineUid,
                now);
        verifyNoInteractions(proofPort);
    }

    private static String obsoleteProgressPayload() {
        return """
                {
                  "trustedSource": {
                    "productId": "product",
                    "deviceName": "test-device-1"
                  },
                  "eventCanonicalSha256":
                    "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                  "event": {
                    "schemaVersion": 2,
                    "eventUid": "10000000-0000-4000-8000-000000000001",
                    "edgeEventSequence": 1,
                    "eventType": "CONFIGURATION_PROGRESS",
                    "deliveryClass": "RELIABLE_FACT",
                    "target": {
                      "type": "CONFIGURATION_APPLICATION",
                      "uid": "10000000-0000-4000-8000-000000000002"
                    },
                    "commandUid": "10000000-0000-4000-8000-000000000003",
                    "occurredAt": "2026-08-09T12:00:00Z",
                    "clockQuality": "SYNCED",
                    "payloadSha256":
                      "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "payload": {
                      "applicationUid":
                        "10000000-0000-4000-8000-000000000002",
                      "stage": "EDGE_SAVED",
                      "version": 1,
                      "contentSha256":
                        "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
                      "mcuPayloadSha256":
                        "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
                      "mcuCommandUid": null,
                      "errorCode": null
                    }
                  }
                }
                """;
    }
}
