package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.device.api.port.ExpiredUnstartedDeviceWorkPort;
import org.enveloping.ecobin.device.api.port.BlockedDeviceCommandBusinessPort;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmissionResult;
import org.enveloping.ecobin.operations.infrastructure.config.ReliableTaskProperties;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository.DeviceTaskExecution;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository.LegacyBaselineEvidenceWait;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ReliableDeviceCommandCompletionServiceTest {

    @Test
    void acceptedBaselineWaitsOnlyForMeasurementAndNetworkEvidence() {
        ReliableOperationsJdbcRepository repository =
                mock(ReliableOperationsJdbcRepository.class);
        LocalDateTime now = LocalDateTime.of(2026, 8, 11, 12, 0);
        UUID taskUid = UUID.randomUUID();
        UUID commandUid = UUID.randomUUID();
        UUID attemptUid = UUID.randomUUID();
        UUID leaseToken = UUID.randomUUID();
        ClaimedDeviceCommandTask claim = new ClaimedDeviceCommandTask(
                taskUid,
                commandUid,
                attemptUid,
                leaseToken,
                0,
                "MEASURE_EMPTY_BAG_BASELINE",
                "SN-BASELINE-DEADLINE",
                """
                        {
                          "expiresAt":"2036-08-08T12:00:00Z",
                          "payload":{"measurementTimeoutMs":6000}
                        }
                        """,
                new byte[32],
                now,
                now.plusMinutes(1));
        when(repository.lockDeviceTaskExecution(
                taskUid, commandUid, attemptUid))
                .thenReturn(new DeviceTaskExecution(
                        41L,
                        "PENDING",
                        leaseToken,
                        now.plusMinutes(1),
                        0,
                        0,
                        0,
                        2,
                        1,
                        51L,
                        leaseToken,
                        0,
                        null));
        when(repository.databaseNow()).thenReturn(now);
        ReliableDeviceCommandCompletionService service =
                new ReliableDeviceCommandCompletionService(
                        repository,
                        new ReliableTaskProperties(),
                        new ObjectMapper(),
                        List.of(),
                        List.of());

        service.complete(
                claim,
                new DeviceCommandSubmissionResult(
                        DeviceCommandSubmissionResult.Outcome
                                .PLATFORM_ACCEPTED,
                        null,
                        null,
                        200,
                        null,
                        null),
                12);

        verify(repository).scheduleAwaitingDeviceEvidence(
                41L, now.plusSeconds(36), now);
    }

    @Test
    void legacyTenYearBaselineWaitIsClosedWithoutManualDatabaseChange() {
        ReliableOperationsJdbcRepository repository =
                mock(ReliableOperationsJdbcRepository.class);
        BlockedDeviceCommandBusinessPort baseline =
                mock(BlockedDeviceCommandBusinessPort.class);
        LocalDateTime now = LocalDateTime.of(2026, 8, 11, 12, 0);
        UUID commandUid = UUID.randomUUID();
        when(repository.databaseNow()).thenReturn(now);
        when(repository.lockLegacyBaselineEvidenceWaits(now))
                .thenReturn(List.of(new LegacyBaselineEvidenceWait(
                        61L,
                        commandUid,
                        "MEASURE_EMPTY_BAG_BASELINE",
                        3,
                        """
                                {
                                  "expiresAt":"2036-08-08T12:00:00Z",
                                  "payload":{"measurementTimeoutMs":6000}
                                }
                                """,
                        now.minusMinutes(1))));
        when(repository.lockExpiredDeviceEvidenceWaits(now))
                .thenReturn(List.of());
        when(repository.lockUnalignedBlockedPhysicalCommands())
                .thenReturn(List.of());
        ReliableDeviceCommandCompletionService service =
                new ReliableDeviceCommandCompletionService(
                        repository,
                        new ReliableTaskProperties(),
                        new ObjectMapper(),
                        List.of(),
                        List.of(baseline));

        int recovered = service.expireEvidenceWaits();

        assertThat(recovered).isEqualTo(1);
        verify(repository).blockExpiredDeviceEvidenceWait(61L, 3, now);
        verify(baseline).applyBlockedDeviceCommand(
                new org.enveloping.ecobin.device.api.result
                        .BlockedDeviceCommand(
                        commandUid,
                        "MEASURE_EMPTY_BAG_BASELINE",
                        org.enveloping.ecobin.device.api.result
                                .BlockedDeviceCommand.Certainty
                                .OUTCOME_UNKNOWN,
                        "DEVICE_EVIDENCE_TIMEOUT",
                        now));
    }

    @Test
    void closesOnlyDomainApprovedUndispatchedWorkThenCancelsItsTask() {
        ReliableOperationsJdbcRepository repository =
                mock(ReliableOperationsJdbcRepository.class);
        ExpiredUnstartedDeviceWorkPort delivery =
                mock(ExpiredUnstartedDeviceWorkPort.class);
        ExpiredUnstartedDeviceWorkPort clean =
                mock(ExpiredUnstartedDeviceWorkPort.class);
        LocalDateTime now = LocalDateTime.of(
                2026, 8, 3, 12, 0);
        when(repository.databaseNow()).thenReturn(now);
        when(delivery.closeExpiredUnstartedWork(now))
                .thenReturn(List.of(101L));
        when(clean.closeExpiredUnstartedWork(now))
                .thenReturn(List.of(202L));
        when(repository.lockExpiredDeviceEvidenceWaits(now))
                .thenReturn(List.of(
                        new ReliableOperationsJdbcRepository
                                .BlockedDeviceCommandRow(
                                303L,
                                UUID.randomUUID(),
                                "REQUEST_DEVICE_ACCEPTANCE",
                                0,
                                "DEVICE_EVIDENCE_TIMEOUT",
                                now)));
        when(repository.lockUnalignedBlockedPhysicalCommands())
                .thenReturn(List.of());
        ReliableDeviceCommandCompletionService service =
                new ReliableDeviceCommandCompletionService(
                        repository,
                        new ReliableTaskProperties(),
                        new ObjectMapper(),
                        List.of(delivery, clean),
                        List.of());

        int recovered = service.expireEvidenceWaits();

        assertThat(recovered).isEqualTo(3);
        verify(repository).cancelExpiredUnstartedDeviceTask(101L, now);
        verify(repository).cancelExpiredUnstartedDeviceTask(202L, now);
        verify(repository).blockExpiredDeviceEvidenceWait(303L, 0, now);
    }
}
