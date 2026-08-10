package org.enveloping.ecobin.device.application.target;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/** Resumes the global policy rollout without requiring an operator retry. */
@Component
@ConditionalOnProperty(
        prefix = "ecobin.device.runtime-snapshot-policy",
        name = "scheduler-enabled",
        havingValue = "true",
        matchIfMissing = true)
public class RuntimeSnapshotPolicyRolloutScheduler {

    private static final Logger LOGGER = LoggerFactory.getLogger(
            RuntimeSnapshotPolicyRolloutScheduler.class);

    private final TargetDeviceApplication application;

    public RuntimeSnapshotPolicyRolloutScheduler(
            TargetDeviceApplication application) {
        this.application = application;
    }

    @Scheduled(fixedDelayString =
            "${ecobin.device.runtime-snapshot-policy.rollout-ms:5000}")
    public void reconcile() {
        try {
            application.reconcileRuntimeSnapshotPolicyNextBatch();
        } catch (RuntimeException exception) {
            LOGGER.warn(
                    "runtime snapshot policy rollout failed type={}",
                    exception.getClass().getSimpleName());
        }
    }
}
