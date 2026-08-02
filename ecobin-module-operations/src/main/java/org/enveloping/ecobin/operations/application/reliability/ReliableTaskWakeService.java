package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.framework.reliability.ReliableTaskWake;
import org.enveloping.ecobin.framework.reliability.ReliableTaskWakePort;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.beans.factory.annotation.Autowired;

@Service
public class ReliableTaskWakeService implements ReliableTaskWakePort {

    private final ReliableOperationsJdbcRepository repository;
    private final ReliableWorkSignal workSignal;

    @Autowired
    public ReliableTaskWakeService(
            ReliableOperationsJdbcRepository repository,
            ReliableWorkSignal workSignal) {
        this.repository = repository;
        this.workSignal = workSignal;
    }

    ReliableTaskWakeService(
            ReliableOperationsJdbcRepository repository) {
        this(repository, new ReliableWorkSignal(ignored -> { }));
    }

    @Override
    @Transactional(isolation = Isolation.READ_COMMITTED)
    public long wake(ReliableTaskWake wake) {
        long version = repository.wakeTask(
                wake.taskUid(), repository.databaseNow());
        workSignal.deviceCommand();
        return version;
    }
}
