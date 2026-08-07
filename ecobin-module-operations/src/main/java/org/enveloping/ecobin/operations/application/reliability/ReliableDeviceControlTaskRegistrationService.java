package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.framework.reliability.ReliableDeviceControlTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceControlTaskRegistrationPort;
import org.enveloping.ecobin.framework.reliability.DeviceAssetTaskRef;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.UUID;

@Service
public class ReliableDeviceControlTaskRegistrationService
        implements ReliableDeviceControlTaskRegistrationPort {

    private final ReliableOperationsJdbcRepository repository;
    private final ReliableWorkSignal workSignal;

    public ReliableDeviceControlTaskRegistrationService(
            ReliableOperationsJdbcRepository repository,
            ReliableWorkSignal workSignal) {
        this.repository = repository;
        this.workSignal = workSignal;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public UUID register(
            ReliableDeviceControlTaskRegistration registration) {
        long[] keys = new long[3];
        registration.sourceAsset().writeForeignKeysTo(
                (tenantKey, organizationKey, assetKey) -> {
                    keys[0] = tenantKey;
                    keys[1] = organizationKey;
                    keys[2] = assetKey;
                });
        UUID taskUid = repository.insertDeviceControlTask(
                keys[0],
                keys[1],
                keys[2],
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

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public void cancelPending(
            DeviceAssetTaskRef sourceAsset,
            String taskType,
            String targetType,
            String targetStableKey) {
        long[] keys = new long[3];
        sourceAsset.writeForeignKeysTo(
                (tenantKey, organizationKey, assetKey) -> {
                    keys[0] = tenantKey;
                    keys[1] = organizationKey;
                    keys[2] = assetKey;
                });
        repository.cancelPendingDeviceControlTask(
                keys[0],
                keys[1],
                keys[2],
                taskType,
                targetType,
                targetStableKey,
                repository.databaseNow());
        workSignal.deviceCommand();
    }
}
