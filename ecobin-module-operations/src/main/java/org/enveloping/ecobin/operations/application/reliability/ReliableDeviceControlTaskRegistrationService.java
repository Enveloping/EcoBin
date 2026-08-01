package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.framework.reliability.ReliableDeviceControlTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceControlTaskRegistrationPort;
import org.enveloping.ecobin.framework.reliability.DeviceDeploymentTaskRef;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.UUID;

@Service
public class ReliableDeviceControlTaskRegistrationService
        implements ReliableDeviceControlTaskRegistrationPort {

    private final ReliableOperationsJdbcRepository repository;

    public ReliableDeviceControlTaskRegistrationService(
            ReliableOperationsJdbcRepository repository) {
        this.repository = repository;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public UUID register(
            ReliableDeviceControlTaskRegistration registration) {
        long[] keys = new long[3];
        registration.sourceDeployment().writeForeignKeysTo(
                (tenantKey, organizationKey, deploymentKey) -> {
                    keys[0] = tenantKey;
                    keys[1] = organizationKey;
                    keys[2] = deploymentKey;
                });
        return repository.insertDeviceControlTask(
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
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public void cancelPending(
            DeviceDeploymentTaskRef sourceDeployment,
            String taskType,
            String targetType,
            String targetStableKey) {
        long[] keys = new long[3];
        sourceDeployment.writeForeignKeysTo(
                (tenantKey, organizationKey, deploymentKey) -> {
                    keys[0] = tenantKey;
                    keys[1] = organizationKey;
                    keys[2] = deploymentKey;
                });
        repository.cancelPendingDeviceControlTask(
                keys[0],
                keys[1],
                keys[2],
                taskType,
                targetType,
                targetStableKey,
                repository.databaseNow());
    }
}
