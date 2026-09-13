package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.device.api.port.DeviceCommandCanonicalizationPort;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository.UpgradeTaskToResume;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import tools.jackson.databind.json.JsonMapper;

import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class ReliableDeviceTaskLifecycleServiceTest {
    private final ReliableOperationsJdbcRepository repository = mock(ReliableOperationsJdbcRepository.class);
    private final ReliableWorkSignal signal = mock(ReliableWorkSignal.class);
    private final DeviceCommandCanonicalizationPort canonicalizer = mock(DeviceCommandCanonicalizationPort.class);
    private final JsonMapper mapper = JsonMapper.builder().build();
    private final LocalDateTime now = LocalDateTime.of(2026, 9, 12, 20, 0);
    private final UUID oldTask = UUID.randomUUID();
    private final UUID nextTask = UUID.randomUUID();
    private final UUID deployment = UUID.randomUUID();
    private ReliableDeviceTaskLifecycleService service;
    private String original;

    @BeforeEach
    void setup() {
        service = new ReliableDeviceTaskLifecycleService(repository, signal, mapper, canonicalizer);
        when(repository.requireDeviceAssetId("HW-1")).thenReturn(1L);
        when(repository.databaseNow()).thenReturn(now);
        when(repository.lockDeviceWorkAllowed(1, "START_MCU_FIRMWARE_UPDATE")).thenReturn(true);
        when(canonicalizer.payloadSha256(any())).thenReturn(new byte[32]);
        when(repository.insertPlatformDeviceControlTask(eq(1L), anyString(), anyString(), anyString(), anyString(),
                anyInt(), anyString(), any(), any(), any(), anyInt(), any(), any())).thenReturn(nextTask);
        prepare(now.minusDays(1));
    }

    private void prepare(LocalDateTime expiresAt) {
        original = """
                {"schemaVersion":2,"commandUid":"%s","commandType":"START_MCU_FIRMWARE_UPDATE",
                "issuedAt":"2026-09-10T00:00:00Z","expiresAt":"%s",
                "target":{"type":"MCU_FIRMWARE_DEPLOYMENT","uid":"%s"},
                "payload":{"firmwareVersion":"2.0.0","firmwareVersionCode":20000,"packageSha256":"frozen-package"}}
                """.formatted(UUID.randomUUID(), expiresAt.toInstant(ZoneOffset.UTC), deployment);
        when(repository.upgradeTaskToResume(1L, oldTask)).thenReturn(Optional.of(new UpgradeTaskToResume(
                "START_MCU_FIRMWARE_UPDATE", "MCU_FIRMWARE_DEPLOYMENT", deployment.toString(), 2, original, null, 20)));
    }

    @Test
    void expiredUnsentAuthorizationGetsNewIdentityWhileKeepingTheFrozenTargetAndPayload() {
        var renewed = service.renewExpiredUnsentUpgradeTask("HW-1", oldTask).orElseThrow();
        assertThat(renewed.taskUid()).isEqualTo(nextTask);
        var json = ArgumentCaptor.forClass(String.class);
        verify(repository).insertPlatformDeviceControlTask(eq(1L), eq("START_MCU_FIRMWARE_UPDATE"), anyString(),
                eq("MCU_FIRMWARE_DEPLOYMENT"), eq(deployment.toString()), eq(2), json.capture(), any(), isNull(),
                eq(renewed.commandUid()), eq(20), isNull(), eq(now));
        var next = mapper.readTree(json.getValue());
        assertThat(next.path("payload")).isEqualTo(mapper.readTree(original).path("payload"));
        assertThat(next.path("target")).isEqualTo(mapper.readTree(original).path("target"));
        assertThat(next.path("expiresAt").asText()).isEqualTo("2026-09-12T20:15:00Z");
        assertThat(next.path("commandUid").asText()).isEqualTo(renewed.commandUid().toString());
        verify(repository).cancelReplacedUpgradeTask(oldTask, now);
        verify(signal).deviceCommand();
    }

    @Test
    void anUnexpiredAuthorizationKeepsItsOriginalTask() {
        prepare(now.plusMinutes(4));
        assertThat(service.renewExpiredUnsentUpgradeTask("HW-1", oldTask)).isEmpty();
        verify(repository, never()).cancelReplacedUpgradeTask(any(), any());
        verifyNoInteractions(signal);
    }

    @Test
    void possiblySubmittedCommandIsNeverReauthorized() {
        when(repository.externalCallMayHaveStarted(oldTask)).thenReturn(true);
        assertThat(service.renewExpiredUnsentUpgradeTask("HW-1", oldTask)).isEmpty();
        verify(repository, never()).cancelReplacedUpgradeTask(any(), any());
        verifyNoInteractions(signal);
    }

    @Test
    void failedOrCancelledTasksAreNeverReopened() {
        when(repository.upgradeTaskToResume(1L, oldTask)).thenReturn(Optional.empty());
        assertThat(service.renewExpiredUnsentUpgradeTask("HW-1", oldTask)).isEmpty();
        verify(repository, never()).cancelReplacedUpgradeTask(any(), any());
        verifyNoInteractions(signal);
    }
}
