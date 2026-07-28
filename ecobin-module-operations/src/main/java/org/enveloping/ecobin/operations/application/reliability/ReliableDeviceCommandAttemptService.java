package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

@Service
public class ReliableDeviceCommandAttemptService {

    private final ReliableOperationsJdbcRepository repository;

    public ReliableDeviceCommandAttemptService(
            ReliableOperationsJdbcRepository repository) {
        this.repository = repository;
    }

    @Transactional(
            propagation = Propagation.REQUIRES_NEW,
            isolation = Isolation.READ_COMMITTED)
    public void markExternalCallMayHaveStarted(
            ClaimedDeviceCommandTask claim) {
        repository.markExternalCallMayHaveStarted(
                claim.attemptUid(),
                claim.leaseToken(),
                repository.databaseNow());
    }
}
