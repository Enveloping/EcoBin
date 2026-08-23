package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.device.api.port.FactorySealDispatchAuthorizationPort;
import org.enveloping.ecobin.device.api.result.FactorySealDispatchDecision;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository.DeviceTaskExecution;
import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ReliableDeviceCommandAttemptServiceTest {

    @Test
    void staleSealSnapshotIsCancelledBeforeTheExternalMarker() {
        ReliableOperationsJdbcRepository repository =
                mock(ReliableOperationsJdbcRepository.class);
        FactorySealDispatchAuthorizationPort authorization =
                mock(FactorySealDispatchAuthorizationPort.class);
        ClaimedDeviceCommandTask claim = claim("AUTHORIZE_FACTORY_SEAL");
        LocalDateTime now = LocalDateTime.of(2026, 8, 22, 15, 5);
        when(repository.databaseNow()).thenReturn(now);
        when(authorization.authorizeDispatch(
                claim.taskUid(),
                claim.commandUid(),
                claim.hardwareSn(),
                now)).thenReturn(new FactorySealDispatchDecision(
                FactorySealDispatchDecision.Outcome.CANCEL_STALE,
                "ACCEPTANCE_SNAPSHOT_CHANGED"));
        when(repository.lockDeviceTaskExecution(
                claim.taskUid(),
                claim.commandUid(),
                claim.attemptUid())).thenReturn(execution(claim));
        ReliableDeviceCommandAttemptService service =
                new ReliableDeviceCommandAttemptService(
                        repository, authorization);

        assertThat(service.prepareExternalCall(claim)).isEqualTo(
                ReliableDeviceCommandAttemptService.DispatchPreparation
                        .NO_SUBMISSION);

        verify(repository, never()).markExternalCallMayHaveStarted(
                any(), any(), any());
        verify(repository).recordDeviceAttemptResult(
                eq(71L),
                eq("NO_ACTION_REQUIRED"),
                eq(0L),
                any(),
                any(),
                any(),
                any(),
                eq("ACCEPTANCE_SNAPSHOT_CHANGED"),
                eq(now));
        verify(repository).markTaskCancelled(61L, 3L, now);
        verify(repository, never()).markTaskDone(
                anyLong(), anyLong(), any());
    }

    @Test
    void currentSealSnapshotGetsTheExternalMarkerOnlyAfterAuthorization() {
        ReliableOperationsJdbcRepository repository =
                mock(ReliableOperationsJdbcRepository.class);
        FactorySealDispatchAuthorizationPort authorization =
                mock(FactorySealDispatchAuthorizationPort.class);
        ClaimedDeviceCommandTask claim = claim("AUTHORIZE_FACTORY_SEAL");
        LocalDateTime now = LocalDateTime.of(2026, 8, 22, 15, 6);
        when(repository.databaseNow()).thenReturn(now);
        when(authorization.authorizeDispatch(
                claim.taskUid(),
                claim.commandUid(),
                claim.hardwareSn(),
                now)).thenReturn(new FactorySealDispatchDecision(
                FactorySealDispatchDecision.Outcome.ALLOW,
                null));
        ReliableDeviceCommandAttemptService service =
                new ReliableDeviceCommandAttemptService(
                        repository, authorization);

        assertThat(service.prepareExternalCall(claim)).isEqualTo(
                ReliableDeviceCommandAttemptService.DispatchPreparation
                        .SUBMIT);

        verify(authorization).authorizeDispatch(
                claim.taskUid(),
                claim.commandUid(),
                claim.hardwareSn(),
                now);
        verify(repository).markExternalCallMayHaveStarted(
                claim.attemptUid(), claim.leaseToken(), now);
    }

    private static ClaimedDeviceCommandTask claim(String commandType) {
        LocalDateTime claimedAt = LocalDateTime.of(
                2026, 8, 22, 15, 4);
        return new ClaimedDeviceCommandTask(
                UUID.fromString("10000000-0000-4000-8000-000000000001"),
                UUID.fromString("20000000-0000-4000-8000-000000000001"),
                UUID.fromString("30000000-0000-4000-8000-000000000001"),
                UUID.fromString("40000000-0000-4000-8000-000000000001"),
                3L,
                commandType,
                "SN-CONTRACT-0001",
                "{}",
                new byte[32],
                claimedAt,
                claimedAt.plusSeconds(30));
    }

    private static DeviceTaskExecution execution(
            ClaimedDeviceCommandTask claim) {
        return new DeviceTaskExecution(
                61L,
                "PENDING",
                claim.leaseToken(),
                claim.leaseUntil(),
                3L,
                2L,
                0,
                1000,
                1,
                71L,
                claim.leaseToken(),
                claim.claimedWakeVersion(),
                null);
    }
}
