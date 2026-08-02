package org.enveloping.ecobin.integration.onenet.outbound;

import org.enveloping.ecobin.operations.api.reliability.DeviceTaskGateReconciliationPort;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.concurrent.atomic.AtomicBoolean;

@Component
@ConditionalOnProperty(
        prefix = "ecobin.external",
        name = "mode",
        havingValue = "real")
@ConditionalOnProperty(
        prefix = "ecobin.operations.reliable",
        name = "workers-enabled",
        havingValue = "true",
        matchIfMissing = true)
public class DeviceRuntimeFreshnessWorker {

    private static final Logger LOGGER =
            LoggerFactory.getLogger(DeviceRuntimeFreshnessWorker.class);

    private final DeviceTaskGateReconciliationPort gateService;
    private final AtomicBoolean reconciling = new AtomicBoolean();

    public DeviceRuntimeFreshnessWorker(
            DeviceTaskGateReconciliationPort gateService) {
        this.gateService = gateService;
    }

    @Scheduled(
            fixedDelayString =
                    "${ecobin.device.runtime-presence-reconcile-interval:5s}")
    public void reconcile() {
        if (!reconciling.compareAndSet(false, true)) {
            return;
        }
        try {
            int changed = gateService.reconcileAll();
            if (changed > 0) {
                LOGGER.info(
                        "device online/task gates reconciled changed={}",
                        changed);
            }
        } catch (RuntimeException failure) {
            LOGGER.error(
                    "device online/task gate reconciliation failed type={}",
                    failure.getClass().getSimpleName());
        } finally {
            reconciling.set(false);
        }
    }
}
