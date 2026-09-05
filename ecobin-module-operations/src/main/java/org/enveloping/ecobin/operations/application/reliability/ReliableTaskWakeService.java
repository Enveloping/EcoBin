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

    private static final String BUSINESS_DOWNLOAD_AUTHORIZATION_REQUIRED =
            "BUSINESS_DOWNLOAD_AUTHORIZATION_REQUIRED";
    private static final String MCU_FIRMWARE_PACKAGE_FETCH_FAILED =
            "MCU_FIRMWARE_PACKAGE_FETCH_FAILED";

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
        long version = switch (wake.evidenceKind()) {
            case BUSINESS_DOWNLOAD_AUTHORIZATION_REQUIRED ->
                    repository.wakeTaskForAuthorizedRedelivery(
                            wake.taskUid(),
                            "START_BUSINESS_RUNTIME_UPDATE",
                            repository.databaseNow());
            case MCU_FIRMWARE_PACKAGE_FETCH_FAILED ->
                    repository.wakeTaskForAuthorizedRedelivery(
                            wake.taskUid(),
                            "START_MCU_FIRMWARE_UPDATE",
                            repository.databaseNow());
            default -> repository.wakeTask(
                    wake.taskUid(), repository.databaseNow());
        };
        workSignal.deviceCommand();
        return version;
    }
}
