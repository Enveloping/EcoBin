package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskRegistrationPort;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.UUID;

@Service
public class ReliableDeviceTaskRegistrationService
        implements ReliableDeviceTaskRegistrationPort {

    private final ReliableOperationsJdbcRepository repository;

    public ReliableDeviceTaskRegistrationService(
            ReliableOperationsJdbcRepository repository) {
        this.repository = repository;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public UUID register(ReliableDeviceTaskRegistration registration) {
        long[] keys = new long[4];
        registration.sourceCommand().writeForeignKeysTo(
                (tenantKey,
                 organizationKey,
                 deploymentKey,
                 commandKey) -> {
                    keys[0] = tenantKey;
                    keys[1] = organizationKey;
                    keys[2] = deploymentKey;
                    keys[3] = commandKey;
                });
        var now = repository.databaseNow();
        if (registration.supersedePriorPendingTasks()) {
            repository.cancelSupersededDeviceTasks(
                    keys[0],
                    keys[1],
                    keys[2],
                    registration.taskType(),
                    now);
        }
        return repository.insertDeviceBusinessTask(
                keys[0],
                keys[1],
                keys[2],
                keys[3],
                registration.taskType(),
                registration.taskKey(),
                registration.targetType(),
                registration.targetStableKey(),
                registration.payloadSchemaVersion(),
                registration.redactedExecutionSnapshot(),
                registration.payloadSha256(),
                registration.correlationUid(),
                registration.causationUid(),
                registration.maxAutoAttempts(),
                registration.initialRunAt() == null
                        ? now
                        : registration.initialRunAt(),
                now);
    }
}
