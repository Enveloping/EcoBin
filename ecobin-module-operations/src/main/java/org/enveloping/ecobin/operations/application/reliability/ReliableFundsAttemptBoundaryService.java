package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.funds.api.port.ReliableFundsAttemptBoundaryPort;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableFundsTaskJdbcRepository;
import org.springframework.stereotype.Service;

import java.util.UUID;

@Service
public class ReliableFundsAttemptBoundaryService
        implements ReliableFundsAttemptBoundaryPort {

    private final ReliableFundsTaskJdbcRepository repository;

    public ReliableFundsAttemptBoundaryService(
            ReliableFundsTaskJdbcRepository repository) {
        this.repository = repository;
    }

    @Override
    public boolean taskExternalCallMayHaveStarted(
            UUID taskUid,
            UUID currentAttemptUid) {
        return repository.taskExternalCallMayHaveStarted(
                taskUid, currentAttemptUid);
    }

    @Override
    public void markExternalCallMayHaveStarted(UUID attemptUid) {
        repository.markExternalCallMayHaveStarted(attemptUid);
    }
}
