package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedPlatformDeviceAssetFactEvent;
import org.enveloping.ecobin.device.application.firmware.McuFirmwareRolloutService;
import org.enveloping.ecobin.device.application.delivery.DeliveryRecoveryQuarantineService;
import org.enveloping.ecobin.device.application.remote.RemoteSupportSessionService;
import org.enveloping.ecobin.device.application.software.DeviceSoftwareCompatibilityService;
import org.enveloping.ecobin.framework.reliability.TrustedPlatformInboxRef;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.JsonNode;

import java.time.LocalDateTime;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.contains;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.mockito.Mockito.never;

class TrustedPlatformDeviceAssetFactServiceTest {

    @Test
    @SuppressWarnings("unchecked")
    void unassignedSafetyFactIsConfirmedWithoutOrganizationProjection() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(jdbc.query(
                contains("FROM dev_device_asset"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenReturn(List.of(41L));
        LocalDateTime now = LocalDateTime.of(2026, 8, 10, 1, 30);
        when(jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class))
                .thenReturn(now);

        TrustedPlatformInboxRef sourceInbox =
                mock(TrustedPlatformInboxRef.class);
        when(sourceInbox.use(any())).thenAnswer(invocation -> {
            TrustedPlatformInboxRef.PlatformInboxFunction<Object> function =
                    invocation.getArgument(0);
            return function.apply(7L);
        });
        ReliablePlatformEdgeConfirmationService confirmationService =
                mock(ReliablePlatformEdgeConfirmationService.class);
        TrustedPlatformDeviceAssetFactService service =
                new TrustedPlatformDeviceAssetFactService(
                        jdbc,
                        JsonMapper.builder().build(),
                        confirmationService,
                        mock(RemoteSupportSessionService.class),
                        mock(McuFirmwareRolloutService.class),
                        mock(FactorySealAuthorizationService.class),
                        mock(DeviceSoftwareCompatibilityService.class));

        TrustedDeviceEventApplyResult result = service.apply(
                new TrustedPlatformDeviceAssetFactEvent(
                        sourceInbox,
                        "SAFETY_SENSOR_STATE_CHANGED",
                        2,
                        safetyPayload()));

        assertEquals(TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED,
                result);
        verify(confirmationService).ensureApplied(
                eq(41L),
                eq("test-device-4"),
                eq("10000000-0000-4000-8000-000000000001"),
                eq("a".repeat(64)),
                eq("NO_ACTION_REQUIRED"),
                eq(now));
    }

    @Test
    @SuppressWarnings("unchecked")
    void remoteSupportStatusIsAppliedInsidePlatformInboxTransaction() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(jdbc.query(
                contains("FROM dev_device_asset"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenReturn(List.of(41L));
        LocalDateTime now = LocalDateTime.of(2026, 8, 16, 6, 53, 15);
        when(jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class))
                .thenReturn(now);

        TrustedPlatformInboxRef sourceInbox =
                mock(TrustedPlatformInboxRef.class);
        when(sourceInbox.use(any())).thenAnswer(invocation -> {
            TrustedPlatformInboxRef.PlatformInboxFunction<Object> function =
                    invocation.getArgument(0);
            return function.apply(7L);
        });
        RemoteSupportSessionService remoteSupportSessions =
                mock(RemoteSupportSessionService.class);
        when(remoteSupportSessions.applyStatus(eq(7L), any()))
                .thenReturn(true);
        ReliablePlatformEdgeConfirmationService confirmationService =
                mock(ReliablePlatformEdgeConfirmationService.class);
        TrustedPlatformDeviceAssetFactService service =
                new TrustedPlatformDeviceAssetFactService(
                        jdbc,
                        JsonMapper.builder().build(),
                        confirmationService,
                        remoteSupportSessions,
                        mock(McuFirmwareRolloutService.class),
                        mock(FactorySealAuthorizationService.class),
                        mock(DeviceSoftwareCompatibilityService.class));

        TrustedDeviceEventApplyResult result = service.apply(
                new TrustedPlatformDeviceAssetFactEvent(
                        sourceInbox,
                        "REMOTE_SUPPORT_TUNNEL_STATUS",
                        2,
                        remoteSupportPayload()));

        assertEquals(TrustedDeviceEventApplyResult.APPLIED, result);
        verify(remoteSupportSessions).applyStatus(eq(7L), any());
        verify(confirmationService).ensureApplied(
                eq(41L),
                eq("test-device-4"),
                eq("8b000000-0000-4000-8000-000000000004"),
                eq("a".repeat(64)),
                eq("NO_ACTION_REQUIRED"),
                eq(now));
    }

    @Test
    void factorySealObservationAcknowledgesTheExactPlatformTask() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        LocalDateTime now = LocalDateTime.of(2026, 8, 22, 15, 10);
        when(jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class))
                .thenReturn(now);
        TrustedPlatformInboxRef sourceInbox =
                mock(TrustedPlatformInboxRef.class);
        when(sourceInbox.use(any())).thenAnswer(invocation -> {
            TrustedPlatformInboxRef.PlatformInboxFunction<Object> function =
                    invocation.getArgument(0);
            return function.apply(17L);
        });
        FactorySealAuthorizationService seals =
                mock(FactorySealAuthorizationService.class);
        when(seals.applyTrustedObservation(
                eq("SN-CONTRACT-0001"),
                any(JsonNode.class),
                eq(now))).thenReturn(
                new FactorySealAuthorizationService.ObservationResult(
                        41L, true));
        ReliablePlatformEdgeConfirmationService confirmations =
                mock(ReliablePlatformEdgeConfirmationService.class);
        TrustedPlatformDeviceAssetFactService service =
                new TrustedPlatformDeviceAssetFactService(
                        jdbc,
                        JsonMapper.builder().build(),
                        confirmations,
                        mock(RemoteSupportSessionService.class),
                        mock(McuFirmwareRolloutService.class),
                        seals,
                        mock(DeviceSoftwareCompatibilityService.class));

        TrustedDeviceEventApplyResult result = service.apply(
                new TrustedPlatformDeviceAssetFactEvent(
                        sourceInbox,
                        "DEVICE_COMMAND_OBSERVED",
                        2,
                        factorySealObservationPayload()));

        assertEquals(TrustedDeviceEventApplyResult.APPLIED, result);
        verify(seals).applyTrustedObservation(
                eq("SN-CONTRACT-0001"), any(JsonNode.class), eq(now));
        verify(confirmations).ensureApplied(
                41L,
                "SN-CONTRACT-0001",
                "84000000-0000-4000-8000-000000000001",
                "a".repeat(64),
                "UPDATED",
                now);
        verify(jdbc, never()).query(
                contains("FROM dev_device_asset"),
                any(RowMapper.class),
                any(Object[].class));
    }

    @Test
    void factorySealCompletionAppliesAndConfirmsThePlatformFact() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        LocalDateTime now = LocalDateTime.of(2026, 8, 23, 1, 10);
        when(jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class))
                .thenReturn(now);
        TrustedPlatformInboxRef sourceInbox =
                mock(TrustedPlatformInboxRef.class);
        when(sourceInbox.use(any())).thenAnswer(invocation -> {
            TrustedPlatformInboxRef.PlatformInboxFunction<Object> function =
                    invocation.getArgument(0);
            return function.apply(18L);
        });
        FactorySealAuthorizationService seals =
                mock(FactorySealAuthorizationService.class);
        when(seals.applyTrustedCompletion(
                eq("SN-CONTRACT-0001"),
                any(JsonNode.class),
                eq(now))).thenReturn(
                new FactorySealAuthorizationService.CompletionResult(
                        41L, true));
        ReliablePlatformEdgeConfirmationService confirmations =
                mock(ReliablePlatformEdgeConfirmationService.class);
        TrustedPlatformDeviceAssetFactService service =
                new TrustedPlatformDeviceAssetFactService(
                        jdbc,
                        JsonMapper.builder().build(),
                        confirmations,
                        mock(RemoteSupportSessionService.class),
                        mock(McuFirmwareRolloutService.class),
                        seals,
                        mock(DeviceSoftwareCompatibilityService.class));

        TrustedDeviceEventApplyResult result = service.apply(
                new TrustedPlatformDeviceAssetFactEvent(
                        sourceInbox,
                        "FACTORY_SEAL_COMPLETED",
                        2,
                        factorySealCompletionPayload()));

        assertEquals(TrustedDeviceEventApplyResult.APPLIED, result);
        verify(seals).applyTrustedCompletion(
                eq("SN-CONTRACT-0001"), any(JsonNode.class), eq(now));
        verify(confirmations).ensureApplied(
                41L,
                "SN-CONTRACT-0001",
                "8a000000-0000-4000-8000-00000000000a",
                "b".repeat(64),
                "UPDATED",
                now);
        verify(jdbc, never()).query(
                contains("FROM dev_device_asset"),
                any(RowMapper.class),
                any(Object[].class));
    }

    @Test
    void deliveryRecoveryFactUsesIssueOnlyHandlerBeforeDeviceAssetTargetRule() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        LocalDateTime now = LocalDateTime.of(2026, 9, 10, 3, 10);
        when(jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class))
                .thenReturn(now);
        TrustedPlatformInboxRef sourceInbox =
                mock(TrustedPlatformInboxRef.class);
        when(sourceInbox.use(any())).thenAnswer(invocation -> {
            TrustedPlatformInboxRef.PlatformInboxFunction<Object> function =
                    invocation.getArgument(0);
            return function.apply(19L);
        });
        DeliveryRecoveryQuarantineService quarantines =
                mock(DeliveryRecoveryQuarantineService.class);
        when(quarantines.applyTrusted(eq(19L), any(), eq(now)))
                .thenReturn(
                        new DeliveryRecoveryQuarantineService.ApplyResult(
                                41L, true));
        ReliablePlatformEdgeConfirmationService confirmations =
                mock(ReliablePlatformEdgeConfirmationService.class);
        TrustedPlatformDeviceAssetFactService service =
                new TrustedPlatformDeviceAssetFactService(
                        jdbc,
                        JsonMapper.builder().build(),
                        confirmations,
                        mock(RemoteSupportSessionService.class),
                        mock(McuFirmwareRolloutService.class),
                        mock(FactorySealAuthorizationService.class),
                        mock(DeviceSoftwareCompatibilityService.class),
                        null,
                        quarantines);

        TrustedDeviceEventApplyResult result = service.apply(
                new TrustedPlatformDeviceAssetFactEvent(
                        sourceInbox,
                        "DELIVERY_RECOVERY_QUARANTINED",
                        2,
                        deliveryRecoveryPayload()));

        assertEquals(TrustedDeviceEventApplyResult.APPLIED, result);
        verify(quarantines).applyTrusted(eq(19L), any(), eq(now));
        verify(confirmations).ensureApplied(
                41L,
                "SN-CONTRACT-0001",
                "9a000000-0000-4000-8000-000000000001",
                "e".repeat(64),
                "UPDATED",
                now);
        verify(jdbc, never()).query(
                contains("FROM dev_device_asset"),
                any(RowMapper.class),
                any(Object[].class));
    }

    @Test
    @SuppressWarnings("unchecked")
    void softwareStateFactIsProjectedAndConfirmedInsideItsInboxTask() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(jdbc.query(
                contains("FROM dev_device_asset"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenReturn(List.of(41L));
        LocalDateTime now = LocalDateTime.of(2026, 9, 2, 3, 15);
        when(jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class))
                .thenReturn(now);
        TrustedPlatformInboxRef sourceInbox =
                mock(TrustedPlatformInboxRef.class);
        when(sourceInbox.use(any())).thenAnswer(invocation -> {
            TrustedPlatformInboxRef.PlatformInboxFunction<Object> function =
                    invocation.getArgument(0);
            return function.apply(27L);
        });
        DeviceSoftwareCompatibilityService compatibility =
                mock(DeviceSoftwareCompatibilityService.class);
        when(compatibility.apply(
                eq(27L), eq(41L), any(), any(), eq(now)))
                .thenReturn(new DeviceSoftwareCompatibilityService.ApplyResult(
                        true, true));
        ReliablePlatformEdgeConfirmationService confirmations =
                mock(ReliablePlatformEdgeConfirmationService.class);
        TrustedPlatformDeviceAssetFactService service =
                new TrustedPlatformDeviceAssetFactService(
                        jdbc,
                        JsonMapper.builder().build(),
                        confirmations,
                        mock(RemoteSupportSessionService.class),
                        mock(McuFirmwareRolloutService.class),
                        mock(FactorySealAuthorizationService.class),
                        compatibility);

        TrustedDeviceEventApplyResult result = service.apply(
                new TrustedPlatformDeviceAssetFactEvent(
                        sourceInbox,
                        "DEVICE_SOFTWARE_STATE_REPORTED",
                        2,
                        softwareStatePayload()));

        assertEquals(TrustedDeviceEventApplyResult.APPLIED, result);
        verify(compatibility).apply(
                eq(27L), eq(41L), any(), any(), eq(now));
        verify(confirmations).ensureApplied(
                41L,
                "SN-CONTRACT-0001",
                "8d000000-0000-4000-8000-000000000002",
                "d".repeat(64),
                "UPDATED",
                now);
    }

    private static String safetyPayload() {
        return """
                {
                  "trustedSource": {
                    "productId": "product",
                    "deviceName": "test-device-4"
                  },
                  "eventCanonicalSha256":
                    "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                  "event": {
                    "schemaVersion": 2,
                    "eventUid": "10000000-0000-4000-8000-000000000001",
                    "eventType": "SAFETY_SENSOR_STATE_CHANGED",
                    "target": {
                      "type": "DEVICE_ASSET",
                      "uid": "test-device-4"
                    },
                    "payloadSha256":
                      "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "payload": {
                      "smokeState": "NORMAL",
                      "smokeDataUnavailable": false
                    }
                  }
                }
                """;
    }

    private static String deliveryRecoveryPayload() {
        return """
                {
                  "trustedSource": {
                    "productId": "product",
                    "deviceName": "SN-CONTRACT-0001"
                  },
                  "eventCanonicalSha256":
                    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
                  "event": {
                    "schemaVersion": 2,
                    "eventUid": "9a000000-0000-4000-8000-000000000001",
                    "eventType": "DELIVERY_RECOVERY_QUARANTINED",
                    "target": {
                      "type": "DELIVERY_SESSION",
                      "uid": "fca37401-7b2e-4b42-93cf-2fc8c6d72fb2"
                    },
                    "payloadSha256":
                      "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
                    "commandUid": "9b000000-0000-4000-8000-000000000001",
                    "payload": {}
                  }
                }
                """;
    }

    private static String softwareStatePayload() {
        return """
                {
                  "trustedSource": {
                    "productId": "product",
                    "deviceName": "SN-CONTRACT-0001"
                  },
                  "eventCanonicalSha256":
                    "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                  "event": {
                    "schemaVersion": 2,
                    "eventUid": "8d000000-0000-4000-8000-000000000002",
                    "eventType": "DEVICE_SOFTWARE_STATE_REPORTED",
                    "target": {
                      "type": "DEVICE_ASSET",
                      "uid": "SN-CONTRACT-0001"
                    },
                    "payloadSha256":
                      "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
                    "payload": {}
                  }
                }
                """;
    }

    private static String factorySealObservationPayload() {
        return """
                {
                  "trustedSource": {
                    "productId": "contract-product",
                    "deviceName": "SN-CONTRACT-0001"
                  },
                  "eventCanonicalSha256": "%s",
                  "event": {
                    "schemaVersion": 2,
                    "eventUid": "84000000-0000-4000-8000-000000000001",
                    "edgeEventSequence": 101,
                    "eventType": "DEVICE_COMMAND_OBSERVED",
                    "deliveryClass": "RELIABLE_FACT",
                    "target": {
                      "type": "DEVICE_COMMAND",
                      "uid": "20000000-0000-4000-8000-000000000001"
                    },
                    "commandUid": "20000000-0000-4000-8000-000000000001",
                    "occurredAt": "2026-08-22T15:09:00Z",
                    "clockQuality": "SYNCED",
                    "payloadSha256": "%s",
                    "payload": {
                      "observedCommandType": "AUTHORIZE_FACTORY_SEAL",
                      "stage": "RECEIVED",
                      "mcuCommandUid": null,
                      "errorCode": null
                    }
                  }
                }
                """.formatted("b".repeat(64), "a".repeat(64));
    }

    private static String remoteSupportPayload() {
        return """
                {
                  "trustedSource": {
                    "productId": "product",
                    "deviceName": "test-device-4"
                  },
                  "eventCanonicalSha256":
                    "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                  "event": {
                    "schemaVersion": 2,
                    "eventUid": "8b000000-0000-4000-8000-000000000004",
                    "eventType": "REMOTE_SUPPORT_TUNNEL_STATUS",
                    "target": {
                      "type": "DEVICE_ASSET",
                      "uid": "test-device-4"
                    },
                    "payloadSha256":
                      "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "payload": {
                      "sessionUid": "8b000000-0000-4000-8000-000000000001",
                      "state": "OPEN",
                      "remotePort": 22012
                    }
                  }
                }
                """;
    }

    private static String factorySealCompletionPayload() {
        return """
                {
                  "trustedSource": {
                    "productId": "contract-product",
                    "deviceName": "SN-CONTRACT-0001"
                  },
                  "eventCanonicalSha256": "%s",
                  "event": {
                    "schemaVersion": 2,
                    "eventUid": "8a000000-0000-4000-8000-00000000000a",
                    "edgeEventSequence": 1058,
                    "eventType": "FACTORY_SEAL_COMPLETED",
                    "deliveryClass": "RELIABLE_FACT",
                    "target": {
                      "type": "DEVICE_ASSET",
                      "uid": "SN-CONTRACT-0001"
                    },
                    "commandUid": "8a000000-0000-4000-8000-000000000007",
                    "occurredAt": "2026-08-23T01:09:00Z",
                    "clockQuality": "SYNCED",
                    "payloadSha256": "%s",
                    "payload": {}
                  }
                }
                """.formatted("a".repeat(64), "b".repeat(64));
    }
}
