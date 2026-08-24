package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.port.FactorySealReliableTaskPort;
import org.enveloping.ecobin.device.api.result.FactorySealDispatchDecision;
import org.enveloping.ecobin.framework.reliability.PlatformDeviceAssetTaskRef;
import org.enveloping.ecobin.framework.reliability.PlatformDeviceAssetTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistrationPort;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.mockito.ArgumentCaptor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

import java.time.Duration;
import java.time.LocalDateTime;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.contains;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class FactorySealAuthorizationServiceTest {

    private static final String HARDWARE_SN = "SN-CONTRACT-0001";
    private static final String EVIDENCE_UID =
            "8a000000-0000-4000-8000-000000000001";
    private static final String CHALLENGE_UID =
            "8a000000-0000-4000-8000-000000000003";
    private static final String ACCEPTANCE_COMMAND_UID =
            "8a000000-0000-4000-8000-000000000004";
    private static final String NEWER_EVIDENCE_UID =
            "8a000000-0000-4000-8000-000000000008";
    private static final UUID TASK_UID = UUID.fromString(
            "8a000000-0000-4000-8000-000000000006");
    private static final UUID SEAL_COMMAND_UID = UUID.fromString(
            "8a000000-0000-4000-8000-000000000007");
    private static final String COMPLETION_EVENT_UID =
            "8a000000-0000-4000-8000-00000000000a";

    @Test
    @SuppressWarnings("unchecked")
    void passedGenerationRegistersOneSnapshotBoundReliableCommand()
            throws Exception {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        var assetRow = mock(java.sql.ResultSet.class);
        stubAsset(assetRow, "PASSED", 1L, 2L);
        when(jdbc.query(
                contains("FROM dev_device_asset"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenAnswer(invocation -> List.of(
                        invocation.<RowMapper<Object>>getArgument(1)
                                .mapRow(assetRow, 0)));
        when(jdbc.queryForObject(
                contains("FROM dev_factory_seal_authorization"),
                eq(Integer.class),
                any(Object[].class))).thenReturn(0);
        var evidenceRow = mock(java.sql.ResultSet.class);
        when(evidenceRow.getString("evidence_uid"))
                .thenReturn(EVIDENCE_UID);
        when(evidenceRow.getString("challenge_uid"))
                .thenReturn(CHALLENGE_UID);
        when(evidenceRow.getString("command_uid"))
                .thenReturn(ACCEPTANCE_COMMAND_UID);
        when(evidenceRow.getBytes("evidence_sha256"))
                .thenReturn(bytes(1));
        when(evidenceRow.getLong("factory_bag_revision"))
                .thenReturn(2L);
        when(evidenceRow.getBytes("factory_bag_set_sha256"))
                .thenReturn(bytes(2));
        when(jdbc.query(
                contains("FROM dev_device_acceptance_evidence"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenAnswer(invocation -> List.of(
                        invocation.<RowMapper<Object>>getArgument(1)
                                .mapRow(evidenceRow, 0)));
        LocalDateTime now = LocalDateTime.of(2026, 8, 22, 15, 0);
        when(jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class))
                .thenReturn(now);
        when(jdbc.update(
                contains("INSERT INTO dev_factory_seal_authorization"),
                any(Object[].class))).thenReturn(1);
        ReliablePlatformDeviceControlTaskRegistrationPort tasks =
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class);
        when(tasks.register(any())).thenReturn(TASK_UID);
        PlatformDeviceAssetTaskRefFactory refs =
                mock(PlatformDeviceAssetTaskRefFactory.class);
        when(refs.issue(41L)).thenReturn(
                mock(PlatformDeviceAssetTaskRef.class));
        FactorySealAuthorizationService service = service(
                jdbc, refs, tasks, mock(FactorySealReliableTaskPort.class));

        assertThat(service.ensureForAcceptedAsset(41L)).isTrue();

        ArgumentCaptor<ReliablePlatformDeviceControlTaskRegistration>
                registration = ArgumentCaptor.forClass(
                ReliablePlatformDeviceControlTaskRegistration.class);
        verify(tasks).register(registration.capture());
        assertThat(registration.getValue().taskType())
                .isEqualTo("AUTHORIZE_FACTORY_SEAL");
        assertThat(registration.getValue().taskKey())
                .isEqualTo("AUTHORIZE_FACTORY_SEAL:ASSET:41:1");
        assertThat(registration.getValue().maxAutoAttempts())
                .isEqualTo(1000);
        JsonNode envelope = JsonMapper.builder().build().readTree(
                registration.getValue().executionEnvelope());
        assertThat(envelope.path("targetDeviceName").asText())
                .isEqualTo(HARDWARE_SN);
        assertThat(envelope.path("payload")
                .path("acceptanceGeneration").asLong()).isEqualTo(1L);
        assertThat(envelope.path("payload")
                .path("acceptanceEvidenceUid").asText())
                .isEqualTo(EVIDENCE_UID);
        assertThat(envelope.path("payload")
                .path("factoryBagRevision").asLong()).isEqualTo(2L);
        assertThat(envelope.path("cosGrant").isNull()).isTrue();
    }

    @Test
    @SuppressWarnings("unchecked")
    void nonPassedAssetNeverRegistersSealAuthority() throws Exception {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        var assetRow = mock(java.sql.ResultSet.class);
        stubAsset(assetRow, "PENDING", 0L, 2L);
        when(jdbc.query(
                contains("FROM dev_device_asset"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenAnswer(invocation -> List.of(
                        invocation.<RowMapper<Object>>getArgument(1)
                                .mapRow(assetRow, 0)));
        ReliablePlatformDeviceControlTaskRegistrationPort tasks =
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class);
        FactorySealAuthorizationService service = service(
                jdbc,
                mock(PlatformDeviceAssetTaskRefFactory.class),
                tasks,
                mock(FactorySealReliableTaskPort.class));

        assertThat(service.ensureForAcceptedAsset(41L)).isFalse();

        verify(tasks, never()).register(any());
    }

    @Test
    @SuppressWarnings("unchecked")
    void changedAcceptanceGenerationCancelsImmediatelyBeforeDispatch()
            throws Exception {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        stubAssetFirstLock(jdbc);
        var row = mock(java.sql.ResultSet.class);
        stubAuthorization(row, "PENDING", 1L, 2L);
        when(row.getLong("current_acceptance_generation"))
                .thenReturn(2L);
        when(jdbc.query(
                contains("FROM dev_factory_seal_authorization authorization"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenAnswer(invocation -> List.of(
                        invocation.<RowMapper<Object>>getArgument(1)
                                .mapRow(row, 0)));
        when(jdbc.update(
                contains("authorization_status = 'CANCELLED'"),
                any(Object[].class))).thenReturn(1);
        FactorySealAuthorizationService service = service(
                jdbc,
                mock(PlatformDeviceAssetTaskRefFactory.class),
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class),
                mock(FactorySealReliableTaskPort.class));

        FactorySealDispatchDecision decision = service.authorizeDispatch(
                TASK_UID,
                UUID.fromString(
                        "8a000000-0000-4000-8000-000000000007"),
                HARDWARE_SN,
                LocalDateTime.of(2026, 8, 22, 15, 1));

        assertThat(decision.outcome()).isEqualTo(
                FactorySealDispatchDecision.Outcome.CANCEL_STALE);
        assertThat(decision.reasonCode())
                .isEqualTo("ACCEPTANCE_SNAPSHOT_CHANGED");
        verify(jdbc).update(
                contains("authorization_status = 'CANCELLED'"),
                any(Object[].class));
        var ordered = inOrder(jdbc);
        ordered.verify(jdbc).query(
                contains("factory-seal-lock-order:asset-first"),
                any(RowMapper.class),
                any(Object[].class));
        ordered.verify(jdbc).query(
                contains("FROM dev_factory_seal_authorization authorization"),
                any(RowMapper.class),
                any(Object[].class));
    }

    @Test
    @SuppressWarnings("unchecked")
    void issuedAuthorizationFreezesEveryAcceptanceSnapshotMutation()
            throws Exception {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(jdbc.query(
                contains("FROM dev_factory_seal_authorization"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenReturn(List.of(51L));
        FactorySealAuthorizationService service = service(
                jdbc,
                mock(PlatformDeviceAssetTaskRefFactory.class),
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class),
                mock(FactorySealReliableTaskPort.class));

        assertThatThrownBy(
                () -> service.requireAcceptanceSnapshotMutable(41L))
                .isInstanceOf(TargetApiException.class)
                .extracting("code")
                .isEqualTo("DEVICE.FACTORY_SEAL_AUTHORITY_ISSUED");

        verify(jdbc).query(
                contains("ORDER BY id"),
                any(RowMapper.class),
                any(Object[].class));
    }

    @Test
    @SuppressWarnings("unchecked")
    void trustedReceivedObservationOnlyConfirmsTransportDelivery()
            throws Exception {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        stubAssetFirstLock(jdbc);
        var row = mock(java.sql.ResultSet.class);
        stubAuthorization(row, "PENDING", 1L, 2L);
        when(row.getLong("asset_id")).thenReturn(41L);
        when(row.getString("reliable_task_uid"))
                .thenReturn(TASK_UID.toString());
        when(jdbc.query(
                contains("authorization.command_uid = ?"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenAnswer(invocation -> List.of(
                        invocation.<RowMapper<Object>>getArgument(1)
                                .mapRow(row, 0)));
        when(jdbc.update(
                contains("authorization_status = 'ACKNOWLEDGED'"),
                any(Object[].class))).thenReturn(1);
        FactorySealReliableTaskPort reliableTasks =
                mock(FactorySealReliableTaskPort.class);
        FactorySealAuthorizationService service = service(
                jdbc,
                mock(PlatformDeviceAssetTaskRefFactory.class),
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class),
                reliableTasks);
        UUID commandUid = UUID.fromString(
                "8a000000-0000-4000-8000-000000000007");
        LocalDateTime receivedAt = LocalDateTime.of(
                2026, 8, 22, 15, 2);
        JsonNode event = JsonMapper.builder().build().readTree("""
                {
                  "commandUid": "%s",
                  "target": {
                    "type": "DEVICE_COMMAND",
                    "uid": "%s"
                  },
                  "payload": {
                    "observedCommandType": "AUTHORIZE_FACTORY_SEAL",
                    "stage": "RECEIVED",
                    "mcuCommandUid": null,
                    "errorCode": null
                  }
                }
                """.formatted(commandUid, commandUid));

        FactorySealAuthorizationService.ObservationResult result =
                service.applyTrustedObservation(
                        HARDWARE_SN, event, receivedAt);

        assertThat(result).isEqualTo(
                new FactorySealAuthorizationService.ObservationResult(
                        41L, false));
        verify(jdbc, never()).update(
                contains("authorization_status = 'ACKNOWLEDGED'"),
                any(Object[].class));
        verify(reliableTasks, never()).completeAcceptedCommand(
                any(), any());
        verify(reliableTasks, never()).cancelTask(
                any(), anyString(), any());
    }

    @Test
    @SuppressWarnings("unchecked")
    void trustedAcceptedObservationAcknowledgesAuthorizationAndTask()
            throws Exception {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        stubAssetFirstLock(jdbc);
        var row = mock(java.sql.ResultSet.class);
        stubAuthorization(row, "PENDING", 1L, 2L);
        when(row.getLong("asset_id")).thenReturn(41L);
        when(row.getString("reliable_task_uid"))
                .thenReturn(TASK_UID.toString());
        when(jdbc.query(
                contains("authorization.command_uid = ?"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenAnswer(invocation -> List.of(
                        invocation.<RowMapper<Object>>getArgument(1)
                                .mapRow(row, 0)));
        when(jdbc.update(
                contains("authorization_status = 'ACKNOWLEDGED'"),
                any(Object[].class))).thenReturn(1);
        FactorySealReliableTaskPort reliableTasks =
                mock(FactorySealReliableTaskPort.class);
        FactorySealAuthorizationService service = service(
                jdbc,
                mock(PlatformDeviceAssetTaskRefFactory.class),
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class),
                reliableTasks);
        UUID commandUid = UUID.fromString(
                "8a000000-0000-4000-8000-000000000007");
        LocalDateTime receivedAt = LocalDateTime.of(
                2026, 8, 22, 15, 2);
        JsonNode event = JsonMapper.builder().build().readTree("""
                {
                  "commandUid": "%s",
                  "target": {
                    "type": "DEVICE_COMMAND",
                    "uid": "%s"
                  },
                  "payload": {
                    "observedCommandType": "AUTHORIZE_FACTORY_SEAL",
                    "stage": "ACCEPTED",
                    "mcuCommandUid": null,
                    "errorCode": null
                  }
                }
                """.formatted(commandUid, commandUid));

        FactorySealAuthorizationService.ObservationResult result =
                service.applyTrustedObservation(
                        HARDWARE_SN, event, receivedAt);

        assertThat(result).isEqualTo(
                new FactorySealAuthorizationService.ObservationResult(
                        41L, true));
        verify(jdbc).update(
                contains("authorization_status = 'ACKNOWLEDGED'"),
                any(Object[].class));
        verify(reliableTasks).completeAcceptedCommand(
                commandUid, receivedAt);
        verify(reliableTasks, never()).cancelTask(
                any(), anyString(), any());
    }

    @Test
    @SuppressWarnings("unchecked")
    void latestEvidenceRejectionReturnsAssetToAutomaticAcceptance()
            throws Exception {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        stubAssetFirstLock(jdbc);
        var authorizationRow = mock(java.sql.ResultSet.class);
        stubAuthorization(authorizationRow, "PENDING", 1L, 2L);
        when(authorizationRow.getLong("asset_id")).thenReturn(41L);
        when(authorizationRow.getString("reliable_task_uid"))
                .thenReturn(TASK_UID.toString());
        when(jdbc.query(
                contains("authorization.command_uid = ?"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenAnswer(invocation -> List.of(
                        invocation.<RowMapper<Object>>getArgument(1)
                                .mapRow(authorizationRow, 0)));
        when(jdbc.query(
                contains("candidate.evidence_sha256"),
                any(RowMapper.class),
                any(Object[].class))).thenReturn(List.of());
        when(jdbc.update(
                contains("authorization_status = 'CANCELLED'"),
                any(Object[].class))).thenReturn(1);
        when(jdbc.update(
                contains("acceptance_status = 'PENDING'"),
                any(Object[].class))).thenReturn(1);
        FactorySealReliableTaskPort reliableTasks =
                mock(FactorySealReliableTaskPort.class);
        ReliablePlatformDeviceControlTaskRegistrationPort registrations =
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class);
        FactorySealAuthorizationService service = service(
                jdbc,
                mock(PlatformDeviceAssetTaskRefFactory.class),
                registrations,
                reliableTasks);
        UUID commandUid = UUID.fromString(
                "8a000000-0000-4000-8000-000000000007");
        LocalDateTime receivedAt = LocalDateTime.of(
                2026, 8, 22, 15, 3);

        FactorySealAuthorizationService.ObservationResult result =
                service.applyTrustedObservation(
                        HARDWARE_SN,
                        observation(
                                commandUid,
                                "REJECTED",
                                "ACCEPTANCE_EVIDENCE_NOT_LATEST"),
                        receivedAt);

        assertThat(result).isEqualTo(
                new FactorySealAuthorizationService.ObservationResult(
                        41L, true));
        verify(reliableTasks).cancelTask(
                TASK_UID,
                "ACCEPTANCE_EVIDENCE_NOT_LATEST",
                receivedAt);
        verify(jdbc).update(
                contains("acceptance_status = 'PENDING'"),
                any(Object[].class));
        verify(registrations, never()).register(any());
    }

    @Test
    @SuppressWarnings("unchecked")
    void latestEvidenceRejectionPromotesAlreadyIngestedNewerFact()
            throws Exception {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        stubAssetFirstLock(jdbc);
        var authorizationRow = mock(java.sql.ResultSet.class);
        stubAuthorization(authorizationRow, "PENDING", 1L, 2L);
        when(authorizationRow.getLong("asset_id")).thenReturn(41L);
        when(authorizationRow.getString("reliable_task_uid"))
                .thenReturn(TASK_UID.toString());
        when(jdbc.query(
                contains("authorization.command_uid = ?"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenAnswer(invocation -> List.of(
                        invocation.<RowMapper<Object>>getArgument(1)
                                .mapRow(authorizationRow, 0)));
        byte[] newerDigest = bytes(9);
        when(jdbc.query(
                contains("candidate.evidence_sha256"),
                any(RowMapper.class),
                any(Object[].class))).thenReturn(List.of(newerDigest));

        var assetRow = mock(java.sql.ResultSet.class);
        stubAsset(assetRow, "PASSED", 2L, 2L);
        when(assetRow.getBytes("acceptance_evidence_sha256"))
                .thenReturn(newerDigest);
        when(jdbc.query(
                contains("FROM dev_device_asset"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenAnswer(invocation -> List.of(
                        invocation.<RowMapper<Object>>getArgument(1)
                                .mapRow(assetRow, 0)));
        when(jdbc.queryForObject(
                contains("FROM dev_factory_seal_authorization"),
                eq(Integer.class),
                any(Object[].class))).thenReturn(0);

        var evidenceRow = mock(java.sql.ResultSet.class);
        when(evidenceRow.getString("evidence_uid"))
                .thenReturn(NEWER_EVIDENCE_UID);
        when(evidenceRow.getString("challenge_uid"))
                .thenReturn(CHALLENGE_UID);
        when(evidenceRow.getString("command_uid"))
                .thenReturn(ACCEPTANCE_COMMAND_UID);
        when(evidenceRow.getBytes("evidence_sha256"))
                .thenReturn(newerDigest);
        when(evidenceRow.getLong("factory_bag_revision"))
                .thenReturn(2L);
        when(evidenceRow.getBytes("factory_bag_set_sha256"))
                .thenReturn(bytes(2));
        when(jdbc.query(
                contains("evidence_sha256 = ?"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenAnswer(invocation -> List.of(
                        invocation.<RowMapper<Object>>getArgument(1)
                                .mapRow(evidenceRow, 0)));
        LocalDateTime receivedAt = LocalDateTime.of(
                2026, 8, 22, 15, 3);
        when(jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class))
                .thenReturn(receivedAt);
        when(jdbc.update(anyString(), any(Object[].class))).thenReturn(1);
        ReliablePlatformDeviceControlTaskRegistrationPort registrations =
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class);
        when(registrations.register(any())).thenReturn(UUID.fromString(
                "8a000000-0000-4000-8000-000000000009"));
        PlatformDeviceAssetTaskRefFactory refs =
                mock(PlatformDeviceAssetTaskRefFactory.class);
        when(refs.issue(41L)).thenReturn(
                mock(PlatformDeviceAssetTaskRef.class));
        FactorySealReliableTaskPort reliableTasks =
                mock(FactorySealReliableTaskPort.class);
        FactorySealAuthorizationService service = service(
                jdbc, refs, registrations, reliableTasks);
        UUID commandUid = UUID.fromString(
                "8a000000-0000-4000-8000-000000000007");

        service.applyTrustedObservation(
                HARDWARE_SN,
                observation(
                        commandUid,
                        "REJECTED",
                        "ACCEPTANCE_EVIDENCE_NOT_LATEST"),
                receivedAt);

        ArgumentCaptor<ReliablePlatformDeviceControlTaskRegistration>
                registration = ArgumentCaptor.forClass(
                ReliablePlatformDeviceControlTaskRegistration.class);
        verify(registrations).register(registration.capture());
        JsonNode envelope = JsonMapper.builder().build().readTree(
                registration.getValue().executionEnvelope());
        assertThat(registration.getValue().taskKey())
                .isEqualTo("AUTHORIZE_FACTORY_SEAL:ASSET:41:2");
        assertThat(envelope.path("payload")
                .path("acceptanceEvidenceUid").asText())
                .isEqualTo(NEWER_EVIDENCE_UID);
        assertThat(envelope.path("payload")
                .path("acceptanceGeneration").asLong()).isEqualTo(2L);
    }

    @ParameterizedTest
    @ValueSource(strings = {"PENDING", "ACKNOWLEDGED"})
    @SuppressWarnings("unchecked")
    void completionMaySealPendingOrAcknowledgedAuthorization(
            String authorizationStatus)
            throws Exception {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        stubAssetFirstLock(jdbc);
        JsonNode event = completionEvent();
        var row = mock(java.sql.ResultSet.class);
        stubCompletionAuthorization(
                row, authorizationStatus, null, null);
        when(jdbc.query(
                contains("authorization.completion_event_uid"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenAnswer(invocation -> List.of(
                        invocation.<RowMapper<Object>>getArgument(1)
                                .mapRow(row, 0)));
        when(jdbc.update(
                contains("authorization_status = 'SEALED'"),
                any(Object[].class))).thenReturn(1);
        FactorySealReliableTaskPort reliableTasks =
                mock(FactorySealReliableTaskPort.class);
        FactorySealAuthorizationService service = service(
                jdbc,
                mock(PlatformDeviceAssetTaskRefFactory.class),
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class),
                reliableTasks);
        LocalDateTime receivedAt = LocalDateTime.of(
                2026, 8, 23, 1, 0);

        FactorySealAuthorizationService.CompletionResult result =
                service.applyTrustedCompletion(
                        HARDWARE_SN, event, receivedAt);

        assertThat(result).isEqualTo(
                new FactorySealAuthorizationService.CompletionResult(
                        41L, true));
        verify(jdbc).update(
                contains("authorization_status = 'SEALED'"),
                any(Object[].class));
        verify(reliableTasks).completeAcceptedCommand(
                SEAL_COMMAND_UID, receivedAt);
        var ordered = inOrder(jdbc);
        ordered.verify(jdbc).query(
                contains("factory-seal-lock-order:asset-first"),
                any(RowMapper.class),
                any(Object[].class));
        ordered.verify(jdbc).query(
                contains("authorization.completion_event_uid"),
                any(RowMapper.class),
                any(Object[].class));
    }

    @Test
    @SuppressWarnings("unchecked")
    void unsyncedSchemaV2CompletionSealsWithoutInventingDeviceTimes()
            throws Exception {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        stubAssetFirstLock(jdbc);
        JsonNode event = unsyncedCompletionEvent();
        var row = mock(java.sql.ResultSet.class);
        stubCompletionAuthorization(row, "ACKNOWLEDGED", null, null);
        when(jdbc.query(
                contains("authorization.completion_event_uid"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenAnswer(invocation -> List.of(
                        invocation.<RowMapper<Object>>getArgument(1)
                                .mapRow(row, 0)));
        when(jdbc.update(
                contains("authorization_status = 'SEALED'"),
                any(Object[].class))).thenReturn(1);
        FactorySealReliableTaskPort reliableTasks =
                mock(FactorySealReliableTaskPort.class);
        FactorySealAuthorizationService service = service(
                jdbc,
                mock(PlatformDeviceAssetTaskRefFactory.class),
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class),
                reliableTasks);
        LocalDateTime receivedAt = LocalDateTime.of(
                2026, 8, 23, 1, 0);

        FactorySealAuthorizationService.CompletionResult result =
                service.applyTrustedCompletion(
                        HARDWARE_SN, event, receivedAt);

        assertThat(result.changed()).isTrue();
        ArgumentCaptor<Object[]> arguments =
                ArgumentCaptor.forClass(Object[].class);
        verify(jdbc).update(
                contains("authorization_status = 'SEALED'"),
                arguments.capture());
        assertThat(arguments.getValue()[7]).isEqualTo("ESTIMATED");
        assertThat(arguments.getValue()[8]).isNull();
        assertThat(arguments.getValue()[9]).isNull();
        verify(reliableTasks).completeAcceptedCommand(
                SEAL_COMMAND_UID, receivedAt);
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "2026-08-23T01:00:00.001Z",
            "2026-08-23T09:00:00.000Z"
    })
    void completionRejectsEventAfterCleanup(String occurredAt) {
        FactorySealAuthorizationService service = service(
                mock(JdbcTemplate.class),
                mock(PlatformDeviceAssetTaskRefFactory.class),
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class),
                mock(FactorySealReliableTaskPort.class));

        assertThatThrownBy(() -> service.applyTrustedCompletion(
                HARDWARE_SN,
                completionEvent(occurredAt),
                LocalDateTime.of(2026, 8, 23, 9, 1)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining(
                        "event time differs from completed cleanup");
    }

    @Test
    @SuppressWarnings("unchecked")
    void exactCompletionRetryIsIdempotent() throws Exception {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        stubAssetFirstLock(jdbc);
        JsonNode event = completionEvent();
        byte[] payloadDigest = HexFormat.of().parseHex(
                event.path("payloadSha256").asText());
        var row = mock(java.sql.ResultSet.class);
        stubCompletionAuthorization(
                row,
                "SEALED",
                COMPLETION_EVENT_UID,
                payloadDigest);
        when(jdbc.query(
                contains("authorization.completion_event_uid"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenAnswer(invocation -> List.of(
                        invocation.<RowMapper<Object>>getArgument(1)
                                .mapRow(row, 0)));
        FactorySealReliableTaskPort reliableTasks =
                mock(FactorySealReliableTaskPort.class);
        FactorySealAuthorizationService service = service(
                jdbc,
                mock(PlatformDeviceAssetTaskRefFactory.class),
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class),
                reliableTasks);
        LocalDateTime receivedAt = LocalDateTime.of(
                2026, 8, 23, 1, 1);

        FactorySealAuthorizationService.CompletionResult result =
                service.applyTrustedCompletion(
                        HARDWARE_SN, event, receivedAt);

        assertThat(result.changed()).isFalse();
        verify(jdbc, never()).update(
                contains("authorization_status = 'SEALED'"),
                any(Object[].class));
        verify(reliableTasks).completeAcceptedCommand(
                SEAL_COMMAND_UID, receivedAt);
    }

    @Test
    @SuppressWarnings("unchecked")
    void conflictingCompletionCannotReplaceSealedFact() throws Exception {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        stubAssetFirstLock(jdbc);
        JsonNode event = completionEvent();
        var row = mock(java.sql.ResultSet.class);
        stubCompletionAuthorization(
                row,
                "SEALED",
                COMPLETION_EVENT_UID,
                bytes(9));
        when(jdbc.query(
                contains("authorization.completion_event_uid"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenAnswer(invocation -> List.of(
                        invocation.<RowMapper<Object>>getArgument(1)
                                .mapRow(row, 0)));
        FactorySealReliableTaskPort reliableTasks =
                mock(FactorySealReliableTaskPort.class);
        FactorySealAuthorizationService service = service(
                jdbc,
                mock(PlatformDeviceAssetTaskRefFactory.class),
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class),
                reliableTasks);

        assertThatThrownBy(() -> service.applyTrustedCompletion(
                HARDWARE_SN,
                event,
                LocalDateTime.of(2026, 8, 23, 1, 2)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("conflicts");

        verify(reliableTasks, never()).completeAcceptedCommand(any(), any());
    }

    @Test
    @SuppressWarnings("unchecked")
    void cancelledAuthorizationCannotBeRevivedByCompletion()
            throws Exception {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        stubAssetFirstLock(jdbc);
        var row = mock(java.sql.ResultSet.class);
        stubCompletionAuthorization(row, "CANCELLED", null, null);
        when(jdbc.query(
                contains("authorization.completion_event_uid"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenAnswer(invocation -> List.of(
                        invocation.<RowMapper<Object>>getArgument(1)
                                .mapRow(row, 0)));
        FactorySealReliableTaskPort reliableTasks =
                mock(FactorySealReliableTaskPort.class);
        FactorySealAuthorizationService service = service(
                jdbc,
                mock(PlatformDeviceAssetTaskRefFactory.class),
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class),
                reliableTasks);

        assertThatThrownBy(() -> service.applyTrustedCompletion(
                HARDWARE_SN,
                completionEvent(),
                LocalDateTime.of(2026, 8, 23, 1, 3)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("cannot be revived");

        verify(reliableTasks, never()).completeAcceptedCommand(any(), any());
    }

    @Test
    @SuppressWarnings("unchecked")
    void lateAcceptedObservationCannotDowngradeSealedAuthorization()
            throws Exception {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        stubAssetFirstLock(jdbc);
        var row = mock(java.sql.ResultSet.class);
        stubAuthorization(row, "SEALED", 1L, 2L);
        when(row.getLong("asset_id")).thenReturn(41L);
        when(row.getString("reliable_task_uid"))
                .thenReturn(TASK_UID.toString());
        when(jdbc.query(
                contains("authorization.command_uid = ?"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenAnswer(invocation -> List.of(
                        invocation.<RowMapper<Object>>getArgument(1)
                                .mapRow(row, 0)));
        FactorySealReliableTaskPort reliableTasks =
                mock(FactorySealReliableTaskPort.class);
        FactorySealAuthorizationService service = service(
                jdbc,
                mock(PlatformDeviceAssetTaskRefFactory.class),
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class),
                reliableTasks);
        LocalDateTime receivedAt = LocalDateTime.of(
                2026, 8, 23, 1, 4);

        FactorySealAuthorizationService.ObservationResult result =
                service.applyTrustedObservation(
                        HARDWARE_SN,
                        observation(SEAL_COMMAND_UID, "ACCEPTED", null),
                        receivedAt);

        assertThat(result.changed()).isFalse();
        verify(jdbc, never()).update(
                contains("authorization_status = 'ACKNOWLEDGED'"),
                any(Object[].class));
        verify(reliableTasks).completeAcceptedCommand(
                SEAL_COMMAND_UID, receivedAt);
    }

    @Test
    @SuppressWarnings("unchecked")
    void lateRejectedObservationCannotDowngradeSealedAuthorization()
            throws Exception {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        stubAssetFirstLock(jdbc);
        var row = mock(java.sql.ResultSet.class);
        stubAuthorization(row, "SEALED", 1L, 2L);
        when(row.getLong("asset_id")).thenReturn(41L);
        when(row.getString("reliable_task_uid"))
                .thenReturn(TASK_UID.toString());
        when(jdbc.query(
                contains("authorization.command_uid = ?"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenAnswer(invocation -> List.of(
                        invocation.<RowMapper<Object>>getArgument(1)
                                .mapRow(row, 0)));
        FactorySealReliableTaskPort reliableTasks =
                mock(FactorySealReliableTaskPort.class);
        FactorySealAuthorizationService service = service(
                jdbc,
                mock(PlatformDeviceAssetTaskRefFactory.class),
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class),
                reliableTasks);

        assertThatThrownBy(() -> service.applyTrustedObservation(
                HARDWARE_SN,
                observation(
                        SEAL_COMMAND_UID,
                        "REJECTED",
                        "DEVICE_REJECTED_AUTHORIZATION"),
                LocalDateTime.of(2026, 8, 23, 1, 5)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("cannot be rejected");

        verify(jdbc, never()).update(
                contains("authorization_status = 'CANCELLED'"),
                any(Object[].class));
        verify(reliableTasks, never()).cancelTask(
                any(), anyString(), any());
    }

    @Test
    @SuppressWarnings("unchecked")
    void assignmentGateRequiresExactCurrentGenerationSealed()
            throws Exception {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(jdbc.query(
                contains("factory-seal-assignment-gate"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenReturn(List.of());
        FactorySealAuthorizationService service = service(
                jdbc,
                mock(PlatformDeviceAssetTaskRefFactory.class),
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class),
                mock(FactorySealReliableTaskPort.class));

        assertThatThrownBy(() -> service.requireCurrentGenerationSealed(
                41L, HARDWARE_SN))
                .isInstanceOf(TargetApiException.class)
                .extracting("code")
                .isEqualTo("DEVICE.FACTORY_SEAL_REQUIRED");

        when(jdbc.query(
                contains("factory-seal-assignment-gate"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenReturn(List.of(51L));
        service.requireCurrentGenerationSealed(41L, HARDWARE_SN);
    }

    @Test
    void tenantAndOrganizationAssignmentsBothInvokeSealGate()
            throws Exception {
        Path source = Path.of(
                "src/main/java/org/enveloping/ecobin/device/application/target",
                "TargetDeviceApplication.java");
        if (!Files.isRegularFile(source)) {
            source = Path.of("ecobin-module-device").resolve(source);
        }
        String implementation = Files.readString(
                source, StandardCharsets.UTF_8);
        assertThat(implementation)
                .contains("private CommandResult<DeviceAssetView> assignTenant(")
                .contains("private CommandResult<DeviceAssetView> assignOrganization(");
        assertThat(countOccurrences(
                implementation,
                "factorySealAuthorizations.requireCurrentGenerationSealed("))
                .isEqualTo(2);
    }

    private static JsonNode observation(
            UUID commandUid, String stage, String errorCode)
            throws Exception {
        String errorJson = errorCode == null
                ? "null" : "\"" + errorCode + "\"";
        return JsonMapper.builder().build().readTree("""
                {
                  "commandUid": "%s",
                  "target": {
                    "type": "DEVICE_COMMAND",
                    "uid": "%s"
                  },
                  "payload": {
                    "observedCommandType": "AUTHORIZE_FACTORY_SEAL",
                    "stage": "%s",
                    "mcuCommandUid": null,
                    "errorCode": %s
                  }
                }
                """.formatted(commandUid, commandUid, stage, errorJson));
    }

    private static FactorySealAuthorizationService service(
            JdbcTemplate jdbc,
            PlatformDeviceAssetTaskRefFactory refs,
            ReliablePlatformDeviceControlTaskRegistrationPort tasks,
            FactorySealReliableTaskPort reliableTasks) {
        return new FactorySealAuthorizationService(
                jdbc,
                JsonMapper.builder().build(),
                new DeviceConfigurationCanonicalizer(),
                refs,
                tasks,
                reliableTasks,
                Duration.ofDays(365));
    }

    @SuppressWarnings("unchecked")
    private static void stubAssetFirstLock(JdbcTemplate jdbc) {
        when(jdbc.query(
                contains("factory-seal-lock-order:asset-first"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenReturn(List.of(41L));
    }

    private static void stubAsset(
            java.sql.ResultSet row,
            String status,
            long generation,
            long bagRevision) throws Exception {
        when(row.getLong("id")).thenReturn(41L);
        when(row.getString("hardware_sn")).thenReturn(HARDWARE_SN);
        when(row.getString("acceptance_status")).thenReturn(status);
        when(row.getLong("acceptance_generation"))
                .thenReturn(generation);
        when(row.getBytes("acceptance_evidence_sha256"))
                .thenReturn(bytes(1));
        when(row.getLong("factory_bag_revision"))
                .thenReturn(bagRevision);
        when(row.getBytes("factory_bag_set_sha256"))
                .thenReturn(bytes(2));
    }

    private static void stubAuthorization(
            java.sql.ResultSet row,
            String status,
            long generation,
            long bagRevision) throws Exception {
        when(row.getLong("id")).thenReturn(51L);
        when(row.getString("authorization_status")).thenReturn(status);
        when(row.getString("hardware_sn_snapshot"))
                .thenReturn(HARDWARE_SN);
        when(row.getLong("acceptance_generation"))
                .thenReturn(generation);
        when(row.getString("acceptance_evidence_uid"))
                .thenReturn(EVIDENCE_UID);
        when(row.getBytes("acceptance_evidence_sha256"))
                .thenReturn(bytes(1));
        when(row.getLong("factory_bag_revision"))
                .thenReturn(bagRevision);
        when(row.getBytes("factory_bag_set_sha256"))
                .thenReturn(bytes(2));
        when(row.getString("hardware_sn")).thenReturn(HARDWARE_SN);
        when(row.getString("acceptance_status")).thenReturn("PASSED");
        when(row.getLong("current_acceptance_generation"))
                .thenReturn(generation);
        when(row.getBytes("current_acceptance_evidence_sha256"))
                .thenReturn(bytes(1));
        when(row.getLong("current_factory_bag_revision"))
                .thenReturn(bagRevision);
        when(row.getBytes("current_factory_bag_set_sha256"))
                .thenReturn(bytes(2));
    }

    private static void stubCompletionAuthorization(
            java.sql.ResultSet row,
            String status,
            String completionEventUid,
            byte[] completionPayloadSha256) throws Exception {
        stubAuthorization(row, status, 1L, 2L);
        when(row.getLong("asset_id")).thenReturn(41L);
        when(row.getString("command_uid"))
                .thenReturn(SEAL_COMMAND_UID.toString());
        when(row.getString("reliable_task_uid"))
                .thenReturn(TASK_UID.toString());
        when(row.getString("acceptance_challenge_uid"))
                .thenReturn(CHALLENGE_UID);
        when(row.getString("completion_event_uid"))
                .thenReturn(completionEventUid);
        when(row.getBytes("completion_payload_sha256"))
                .thenReturn(completionPayloadSha256);
    }

    private static JsonNode completionEvent() {
        return completionEvent("2026-08-23T01:00:00.000Z");
    }

    private static JsonNode completionEvent(String occurredAt) {
        DeviceConfigurationCanonicalizer canonicalizer =
                new DeviceConfigurationCanonicalizer();
        String acceptanceSha = HexFormat.of().formatHex(bytes(1));
        String bagSha = HexFormat.of().formatHex(bytes(2));
        Map<String, Object> binding = new LinkedHashMap<>();
        binding.put("commandUid", SEAL_COMMAND_UID.toString());
        binding.put("hardwareSn", HARDWARE_SN);
        binding.put("acceptanceGeneration", 1L);
        binding.put("acceptanceEvidenceUid", EVIDENCE_UID);
        binding.put("acceptanceChallengeUid", CHALLENGE_UID);
        binding.put("acceptanceEvidenceSha256", acceptanceSha);
        binding.put("factoryBagRevision", 2L);
        binding.put("factoryBagSetSha256", bagSha);
        binding.put("imageReleaseId", "ecobin-opiz3-2026.08.22.1");
        binding.put("imageReleaseSha256", "3".repeat(64));
        binding.put("factoryReportSha256", "4".repeat(64));

        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("sealCompletionSchemaVersion", 1);
        payload.put("hardwareSn", HARDWARE_SN);
        payload.put(
                "authorizationCommandUid", SEAL_COMMAND_UID.toString());
        payload.put("acceptanceGeneration", 1L);
        payload.put("acceptanceEvidenceUid", EVIDENCE_UID);
        payload.put("acceptanceEvidenceSha256", acceptanceSha);
        payload.put("acceptanceChallengeUid", CHALLENGE_UID);
        payload.put("factoryBagRevision", 2L);
        payload.put("factoryBagSetSha256", bagSha);
        payload.put("imageReleaseId", "ecobin-opiz3-2026.08.22.1");
        payload.put("imageReleaseSha256", "3".repeat(64));
        payload.put("factoryReportSha256", "4".repeat(64));
        payload.put(
                "authorizationBindingSha256",
                canonicalizer.hex(canonicalizer.payloadSha256(binding)));
        payload.put(
                "operatorConfirmationUid",
                "8a000000-0000-4000-8000-00000000000b");
        payload.put("sealedAt", "2026-08-23T00:59:50.000Z");
        payload.put(
                "cleanupCompletedAt", "2026-08-23T01:00:00.000Z");

        Map<String, Object> target = new LinkedHashMap<>();
        target.put("type", "DEVICE_ASSET");
        target.put("uid", HARDWARE_SN);
        Map<String, Object> event = new LinkedHashMap<>();
        event.put("eventType", "FACTORY_SEAL_COMPLETED");
        event.put("eventUid", COMPLETION_EVENT_UID);
        event.put("commandUid", SEAL_COMMAND_UID.toString());
        event.put("target", target);
        event.put("occurredAt", occurredAt);
        event.put("clockQuality", "SYNCED");
        event.put(
                "payloadSha256",
                canonicalizer.hex(canonicalizer.payloadSha256(payload)));
        event.put("payload", payload);
        return JsonMapper.builder().build().valueToTree(event);
    }

    @SuppressWarnings("unchecked")
    private static JsonNode unsyncedCompletionEvent() {
        DeviceConfigurationCanonicalizer canonicalizer =
                new DeviceConfigurationCanonicalizer();
        JsonNode event = completionEvent();
        Map<String, Object> payload = JsonMapper.builder().build()
                .convertValue(event.path("payload"), Map.class);
        payload.put("sealCompletionSchemaVersion", 2);
        payload.put("completionClockQuality", "ESTIMATED");
        payload.put("sealedAt", null);
        payload.put("cleanupCompletedAt", null);
        ((tools.jackson.databind.node.ObjectNode) event)
                .putNull("occurredAt")
                .put("clockQuality", "ESTIMATED")
                .put(
                        "payloadSha256",
                        canonicalizer.hex(
                                canonicalizer.payloadSha256(payload)))
                .set("payload", JsonMapper.builder().build()
                        .valueToTree(payload));
        return event;
    }

    private static byte[] bytes(int value) {
        byte[] bytes = new byte[32];
        java.util.Arrays.fill(bytes, (byte) value);
        return bytes;
    }

    private static int countOccurrences(String value, String needle) {
        int count = 0;
        int offset = 0;
        while ((offset = value.indexOf(needle, offset)) >= 0) {
            count++;
            offset += needle.length();
        }
        return count;
    }
}
