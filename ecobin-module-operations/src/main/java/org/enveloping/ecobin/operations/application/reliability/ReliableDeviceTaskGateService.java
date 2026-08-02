package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.enveloping.ecobin.operations.api.reliability.DeviceTaskGateReconciliationPort;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

@Service
public class ReliableDeviceTaskGateService
        implements DeviceTaskGateReconciliationPort {

    private final ReliableOperationsJdbcRepository repository;
    private final ReliableWorkSignal workSignal;

    public ReliableDeviceTaskGateService(
            ReliableOperationsJdbcRepository repository,
            ReliableWorkSignal workSignal) {
        this.repository = repository;
        this.workSignal = workSignal;
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public int reconcileAsset(long assetId) {
        var now = repository.databaseNow();
        int readiness = repository.reconcileDeploymentRuntimeFreshness(
                assetId, now);
        int tasks = repository.reconcileDeviceTaskGates(assetId, now);
        int changed = readiness + tasks;
        if (changed > 0) {
            workSignal.deviceCommand();
        }
        return changed;
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public int reconcileHardwareSn(String hardwareSn) {
        return reconcileAsset(repository.requireDeviceAssetId(hardwareSn));
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    @Override
    public int reconcileAll() {
        var now = repository.databaseNow();
        int readiness = repository.reconcileDeploymentRuntimeFreshness(
                null, now);
        int tasks = repository.reconcileDeviceTaskGates(null, now);
        int changed = readiness + tasks;
        if (changed > 0) {
            workSignal.deviceCommand();
        }
        return changed;
    }
}
