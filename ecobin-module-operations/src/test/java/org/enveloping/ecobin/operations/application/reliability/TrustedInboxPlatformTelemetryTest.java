package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.operations.api.inbox.TrustedInboxExecutionLane;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxMessage;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxReceipt;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxReceiptState;
import org.enveloping.ecobin.operations.infrastructure.config.ReliableTaskProperties;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository.InboxAggregate;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;

import java.nio.charset.StandardCharsets;
import java.time.LocalDateTime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class TrustedInboxPlatformTelemetryTest {

    @Test
    void platformRuntimeSnapshotIsProcessedWithoutOrganizationProjection() {
        ReliableOperationsJdbcRepository repository =
                mock(ReliableOperationsJdbcRepository.class);
        CanonicalJson canonicalJson = new CanonicalJson(
                JsonMapper.builder().build());
        ReliableTaskProperties properties =
                mock(ReliableTaskProperties.class);
        LocalDateTime now = LocalDateTime.of(2026, 8, 10, 1, 0);
        when(repository.databaseNow()).thenReturn(now);
        when(repository.lockInboxByUid(any())).thenAnswer(invocation ->
                new InboxAggregate(
                        41L,
                        invocation.getArgument(0),
                        "PLATFORM",
                        null,
                        null,
                        "DEVICE_RUNTIME_SNAPSHOT",
                        2,
                        "{}",
                        "a".repeat(64),
                        "RECEIVED",
                        null,
                        null,
                        null,
                        null));

        TrustedInboxService service = new TrustedInboxService(
                repository, canonicalJson, properties);
        TrustedInboxReceipt receipt = service.receive(
                new TrustedInboxMessage(
                        "onenet.device-event",
                        "onenet:registered-device",
                        "10000000-0000-4000-8000-000000000001",
                        "DEVICE_RUNTIME_SNAPSHOT",
                        2,
                        "encrypted-body".getBytes(StandardCharsets.UTF_8),
                        "{}",
                        "ONENET_PULSAR_AES",
                        "product:test;device:test-device-4",
                        null,
                        null,
                        TrustedInboxExecutionLane.DEVICE));

        assertEquals(TrustedInboxReceiptState.TELEMETRY_APPLIED,
                receipt.state());
        assertNull(receipt.taskUid());
        verify(repository).markInboxProcessed(41L, now);
        verify(repository, never()).insertProcessInboxTask(
                anyLong(), any(), any(), any(), any(), any(), any(),
                any(), any(), any(), anyInt(), any());
    }
}
