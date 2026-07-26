package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.framework.reliability.ReliableTaskWake;
import org.enveloping.ecobin.framework.reliability.ReliableTaskWakePort;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

@Service
public class ReliableTaskWakeService implements ReliableTaskWakePort {

    private final ReliableOperationsJdbcRepository repository;

    public ReliableTaskWakeService(
            ReliableOperationsJdbcRepository repository) {
        this.repository = repository;
    }

    @Override
    @Transactional(isolation = Isolation.READ_COMMITTED)
    public long wake(ReliableTaskWake wake) {
        return repository.wakeTask(wake.taskUid(), repository.databaseNow());
    }
}
