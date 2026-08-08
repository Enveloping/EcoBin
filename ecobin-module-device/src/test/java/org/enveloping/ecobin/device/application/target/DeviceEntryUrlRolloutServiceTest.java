package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.framework.reliability.PlatformDeviceAssetTaskRef;
import org.enveloping.ecobin.framework.reliability.PlatformDeviceAssetTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistrationPort;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.sql.ResultSet;
import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.contains;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class DeviceEntryUrlRolloutServiceTest {

    private static final String BASE_URL =
            "https://www.jinshoubao.com/device-entry/";
    private static final String DEVICE_CODE =
            "Dv_0123456789abcdefghijklmn";

    @Test
    void fakeModeDoesNotCreateRolloutStateOrTasks() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        ReliablePlatformDeviceControlTaskRegistrationPort registration =
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class);
        DeviceEntryUrlRolloutService service = service(
                jdbc,
                mock(PlatformDeviceAssetTaskRefFactory.class),
                registration,
                "fake");

        assertEquals(false, service.reconcileNextBatch());

        verifyNoInteractions(jdbc, registration);
    }

    @Test
    @SuppressWarnings("unchecked")
    void matchingBaseUrlRegistersOneResumablePermanentAssetTask()
            throws Exception {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        PlatformDeviceAssetTaskRefFactory taskRefFactory =
                mock(PlatformDeviceAssetTaskRefFactory.class);
        PlatformDeviceAssetTaskRef taskRef =
                mock(PlatformDeviceAssetTaskRef.class);
        when(taskRefFactory.issue(13L)).thenReturn(taskRef);
        ReliablePlatformDeviceControlTaskRegistrationPort registration =
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class);
        DeviceEntryUrlFactory urlFactory =
                new DeviceEntryUrlFactory(BASE_URL);
        LocalDateTime now = LocalDateTime.of(2026, 8, 8, 12, 0);
        UUID rolloutUid =
                UUID.fromString("10000000-0000-4000-8000-000000000001");
        when(jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class))
                .thenReturn(now);

        when(jdbc.query(
                contains("FROM dev_device_entry_url_rollout"),
                any(RowMapper.class)))
                .thenAnswer(invocation -> {
                    RowMapper<Object> mapper = invocation.getArgument(1);
                    ResultSet resultSet = mock(ResultSet.class);
                    when(resultSet.getString("rollout_uid"))
                            .thenReturn(rolloutUid.toString());
                    when(resultSet.getBytes("base_url_sha256"))
                            .thenReturn(urlFactory.baseUrlSha256());
                    when(resultSet.getString("rollout_status"))
                            .thenReturn("PENDING");
                    when(resultSet.getLong("next_asset_id"))
                            .thenReturn(0L);
                    when(resultSet.getObject(
                            "started_at", LocalDateTime.class))
                            .thenReturn(now);
                    return List.of(mapper.mapRow(resultSet, 0));
                });
        when(jdbc.query(
                contains("FROM dev_device_asset"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenAnswer(invocation -> {
                    RowMapper<Object> mapper = invocation.getArgument(1);
                    ResultSet resultSet = mock(ResultSet.class);
                    when(resultSet.getLong("id")).thenReturn(13L);
                    when(resultSet.getString("hardware_sn"))
                            .thenReturn("test-device-1");
                    when(resultSet.getString("device_public_code"))
                            .thenReturn(DEVICE_CODE);
                    when(resultSet.getString("lifecycle_status"))
                            .thenReturn("IN_STOCK");
                    return List.of(mapper.mapRow(resultSet, 0));
                });

        DeviceEntryUrlRolloutService service = new DeviceEntryUrlRolloutService(
                jdbc,
                new ObjectMapper(),
                new DeviceConfigurationCanonicalizer(),
                urlFactory,
                taskRefFactory,
                registration,
                "real");

        assertTrue(service.reconcileNextBatch());

        ArgumentCaptor<ReliablePlatformDeviceControlTaskRegistration> captor =
                ArgumentCaptor.forClass(
                        ReliablePlatformDeviceControlTaskRegistration.class);
        verify(registration).register(captor.capture());
        ReliablePlatformDeviceControlTaskRegistration task = captor.getValue();
        assertEquals("SYNC_DEVICE_ENTRY_URL", task.taskType());
        assertEquals(
                "SYNC_DEVICE_ENTRY_URL:"
                        + rolloutUid.toString().toUpperCase()
                        + ":13",
                task.taskKey());
        assertEquals(1_000, task.maxAutoAttempts());
        JsonNode envelope = new ObjectMapper().readTree(
                task.executionEnvelope());
        assertEquals("test-device-1",
                envelope.path("targetDeviceName").asText());
        assertEquals(
                BASE_URL + "?deviceCode=" + DEVICE_CODE,
                envelope.path("payload")
                        .path("deviceEntryUrl").asText());
        assertEquals(64,
                envelope.path("payload")
                        .path("deviceEntryUrlSha256").asText().length());
        verify(taskRefFactory).issue(13L);
    }

    private static DeviceEntryUrlRolloutService service(
            JdbcTemplate jdbc,
            PlatformDeviceAssetTaskRefFactory taskRefFactory,
            ReliablePlatformDeviceControlTaskRegistrationPort registration,
            String mode) {
        return new DeviceEntryUrlRolloutService(
                jdbc,
                new ObjectMapper(),
                new DeviceConfigurationCanonicalizer(),
                new DeviceEntryUrlFactory(BASE_URL),
                taskRefFactory,
                registration,
                mode);
    }
}
