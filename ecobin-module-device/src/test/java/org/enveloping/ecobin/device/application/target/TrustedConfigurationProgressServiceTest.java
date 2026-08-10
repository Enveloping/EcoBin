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
import org.springframework.transaction.support.TransactionSynchronizationManager;
import tools.jackson.databind.ObjectMapper;

import java.sql.ResultSet;
import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.contains;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class TrustedConfigurationProgressServiceTest {

    @Test
    @SuppressWarnings("unchecked")
    void lateAppliedEvidenceCorrectsFailedApplicationBeforeActivationReconciliation()
            throws Exception {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(jdbc.query(
                anyString(),
                any(RowMapper.class),
                any(Object[].class)))
                .thenAnswer(invocation -> {
                    String sql = invocation.getArgument(0);
                    if (sql.contains("WHERE event_uid = ?")) {
                        return List.of();
                    }
                    RowMapper<Object> mapper = invocation.getArgument(1);
                    ResultSet rs = mock(ResultSet.class);
                    if (sql.contains("JOIN dev_config_application")) {
                        when(rs.getLong("asset_id")).thenReturn(41L);
                        when(rs.getLong("application_id")).thenReturn(51L);
                        when(rs.getString("application_status"))
                                .thenReturn("FAILED");
                        when(rs.getLong("version_no")).thenReturn(3L);
                        when(rs.getString("content_sha256"))
                                .thenReturn("c".repeat(64));
                        when(rs.getString("mcu_payload_sha256"))
                                .thenReturn("d".repeat(64));
                        when(rs.getLong("command_id")).thenReturn(61L);
                        when(rs.getString("command_state"))
                                .thenReturn("EDGE_ACCEPTED");
                    } else if (sql.contains(
                            "FROM dev_config_application")) {
                        when(rs.getLong("id")).thenReturn(51L);
                    } else if (sql.contains("FROM dev_device_command")) {
                        when(rs.getLong("id")).thenReturn(61L);
                    } else if (sql.contains("FROM dev_device_asset")) {
                        when(rs.getLong("id")).thenReturn(41L);
                    } else {
                        throw new AssertionError(
                                "unexpected query: " + sql);
                    }
                    return List.of(mapper.mapRow(rs, 0));
                });
        LocalDateTime now = LocalDateTime.of(2026, 8, 10, 13, 0);
        when(jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)",
                LocalDateTime.class)).thenReturn(now);
        when(jdbc.queryForObject(
                contains("FROM dev_edge_event"),
                eq(Long.class),
                any(Object[].class))).thenReturn(71L);
        when(jdbc.update(anyString(), any(Object[].class)))
                .thenReturn(1);

        ReliableDeviceTaskProofPort proofPort =
                mock(ReliableDeviceTaskProofPort.class);
        ReliableEdgeConfirmationService confirmationService =
                mock(ReliableEdgeConfirmationService.class);
        TrustedOrganizationInboxRef sourceInbox =
                mock(TrustedOrganizationInboxRef.class);
        when(sourceInbox.use(any())).thenAnswer(invocation -> {
            TrustedOrganizationInboxRef.ScopedInboxFunction<Object> function =
                    invocation.getArgument(0);
            return function.apply(7L, 11L, 13L);
        });
        AutomaticDeviceActivationService activation =
                mock(AutomaticDeviceActivationService.class);
        doThrow(new IllegalStateException("baseline generation mismatch"))
                .when(activation)
                .reconcileInCurrentTransaction(
                        anyLong(),
                        any(UUID.class));
        doThrow(new IllegalStateException("baseline generation mismatch"))
                .when(activation)
                .reconcileAsset(41L);
        TrustedConfigurationProgressService service =
                new TrustedConfigurationProgressService(
                        jdbc,
                        new ObjectMapper(),
                        proofPort,
                        confirmationService,
                        mock(TrustedInboxQuarantinePort.class),
                        mock(TrustedOrganizationInboxRefFactory.class),
                        activation);

        TransactionSynchronizationManager
                .setActualTransactionActive(true);
        TransactionSynchronizationManager.initSynchronization();
        try {
            TrustedDeviceEventApplyResult result = assertDoesNotThrow(
                    () -> service.apply(new TrustedDeviceInboxEvent(
                            sourceInbox,
                            "CONFIGURATION_PROGRESS",
                            2,
                            appliedProgressPayload())));

            assertEquals(TrustedDeviceEventApplyResult.APPLIED, result);
            verify(jdbc).update(
                    contains("status = 'APPLIED'"),
                    any(Object[].class));
            verify(activation, never()).reconcileAsset(41L);
            TransactionSynchronizationManager.getSynchronizations()
                    .forEach(synchronization ->
                            assertDoesNotThrow(
                                    synchronization::afterCommit));
            verify(activation).reconcileAsset(41L);
            verify(activation, never())
                    .reconcileInCurrentTransaction(
                            anyLong(),
                            any(UUID.class));
        } finally {
            TransactionSynchronizationManager.clearSynchronization();
            TransactionSynchronizationManager
                    .setActualTransactionActive(false);
        }
    }

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

    private static String appliedProgressPayload() {
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
                    "eventUid": "10000000-0000-4000-8000-000000000011",
                    "edgeEventSequence": 11,
                    "eventType": "CONFIGURATION_PROGRESS",
                    "deliveryClass": "RELIABLE_FACT",
                    "target": {
                      "type": "CONFIGURATION_APPLICATION",
                      "uid": "10000000-0000-4000-8000-000000000012"
                    },
                    "commandUid": "10000000-0000-4000-8000-000000000013",
                    "occurredAt": "2026-08-10T13:00:00Z",
                    "clockQuality": "SYNCED",
                    "payloadSha256":
                      "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "payload": {
                      "applicationUid":
                        "10000000-0000-4000-8000-000000000012",
                      "stage": "APPLIED",
                      "version": 3,
                      "contentSha256":
                        "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
                      "mcuPayloadSha256":
                        "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
                      "mcuCommandUid":
                        "10000000-0000-4000-8000-000000000014",
                      "errorCode": null
                    }
                  }
                }
                """;
    }
}
