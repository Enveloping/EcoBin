package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedPlatformDeviceAssetFactEvent;
import org.enveloping.ecobin.device.application.remote.RemoteSupportSessionService;
import org.enveloping.ecobin.framework.reliability.TrustedPlatformInboxRef;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import tools.jackson.databind.json.JsonMapper;

import java.time.LocalDateTime;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.contains;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

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
                        mock(RemoteSupportSessionService.class));

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
}
