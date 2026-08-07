package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistrationPort;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.UUID;

@Service
public class ReliablePlatformDeviceControlTaskRegistrationService
        implements ReliablePlatformDeviceControlTaskRegistrationPort {

    private final ReliableOperationsJdbcRepository repository;
    private final ReliableWorkSignal workSignal;

    public ReliablePlatformDeviceControlTaskRegistrationService(
            ReliableOperationsJdbcRepository repository,
            ReliableWorkSignal workSignal) {
        this.repository = repository;
        this.workSignal = workSignal;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public UUID register(
            ReliablePlatformDeviceControlTaskRegistration registration) {
        long[] assetKey = new long[1];
        registration.sourceAsset().writeForeignKeyTo(
                value -> assetKey[0] = value);
        UUID taskUid = repository.insertPlatformDeviceControlTask(
                assetKey[0],
                registration.taskType(),
                registration.taskKey(),
                registration.targetType(),
                registration.targetStableKey(),
                registration.payloadSchemaVersion(),
                registration.executionEnvelope(),
                registration.payloadSha256(),
                registration.correlationUid(),
                registration.causationUid(),
                registration.maxAutoAttempts(),
                repository.databaseNow());
        workSignal.deviceCommand();
        return taskUid;
    }
}
