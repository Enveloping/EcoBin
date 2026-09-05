package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.framework.reliability.ReliableTaskWake;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ReliableTaskWakeServiceTest {

    @Test
    void businessAuthorizationRequestUsesNarrowMaintenanceRedeliveryPath() {
        ReliableOperationsJdbcRepository repository =
                mock(ReliableOperationsJdbcRepository.class);
        UUID taskUid = UUID.randomUUID();
        LocalDateTime now = LocalDateTime.of(2026, 9, 5, 12, 0);
        when(repository.databaseNow()).thenReturn(now);
        when(repository.wakeTaskForAuthorizedRedelivery(
                taskUid, "START_BUSINESS_RUNTIME_UPDATE", now))
                .thenReturn(7L);
        ReliableTaskWakeService service =
                new ReliableTaskWakeService(repository);

        long result = service.wake(new ReliableTaskWake(
                taskUid,
                "BUSINESS_DOWNLOAD_AUTHORIZATION_REQUIRED"));

        assertThat(result).isEqualTo(7L);
        verify(repository).wakeTaskForAuthorizedRedelivery(
                taskUid, "START_BUSINESS_RUNTIME_UPDATE", now);
    }

    @Test
    void ordinaryEvidenceCannotUseMaintenanceRedeliveryPath() {
        ReliableOperationsJdbcRepository repository =
                mock(ReliableOperationsJdbcRepository.class);
        UUID taskUid = UUID.randomUUID();
        LocalDateTime now = LocalDateTime.of(2026, 9, 5, 12, 0);
        when(repository.databaseNow()).thenReturn(now);
        when(repository.wakeTask(taskUid, now)).thenReturn(3L);
        ReliableTaskWakeService service =
                new ReliableTaskWakeService(repository);

        long result = service.wake(new ReliableTaskWake(
                taskUid, "LATE_DOMAIN_FACT"));

        assertThat(result).isEqualTo(3L);
        verify(repository).wakeTask(taskUid, now);
    }
}
