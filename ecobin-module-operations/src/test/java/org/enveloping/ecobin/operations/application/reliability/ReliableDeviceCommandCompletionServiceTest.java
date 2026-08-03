package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.device.api.port.ExpiredUnstartedDeviceWorkPort;
import org.enveloping.ecobin.operations.infrastructure.config.ReliableTaskProperties;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ReliableDeviceCommandCompletionServiceTest {

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
        when(repository.blockExpiredDeviceEvidenceWaits(now))
                .thenReturn(3);
        ReliableDeviceCommandCompletionService service =
                new ReliableDeviceCommandCompletionService(
                        repository,
                        new ReliableTaskProperties(),
                        new ObjectMapper(),
                        List.of(delivery, clean));

        int recovered = service.expireEvidenceWaits();

        assertThat(recovered).isEqualTo(5);
        verify(repository).cancelExpiredUnstartedDeviceTask(101L, now);
        verify(repository).cancelExpiredUnstartedDeviceTask(202L, now);
    }
}
