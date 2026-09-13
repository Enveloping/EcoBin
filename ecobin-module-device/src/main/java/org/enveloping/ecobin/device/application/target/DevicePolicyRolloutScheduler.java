package org.enveloping.ecobin.device.application.target;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/** Resumes the global policy rollout without requiring an operator retry. */
@Component
@ConditionalOnProperty(
        prefix = "ecobin.device.configuration-policy",
        name = "scheduler-enabled",
        havingValue = "true",
        matchIfMissing = true)
public class DevicePolicyRolloutScheduler {

    private static final Logger LOGGER = LoggerFactory.getLogger(
            DevicePolicyRolloutScheduler.class);

    private final TargetDeviceApplication application;

    public DevicePolicyRolloutScheduler(
            TargetDeviceApplication application) {
        this.application = application;
    }

    @Scheduled(fixedDelayString =
            "${ecobin.device.configuration-policy.rollout-ms:5000}")
    public void reconcile() {
        try {
            application.reconcileDevicePolicyNextBatch();
        } catch (RuntimeException exception) {
            LOGGER.warn(
                    "device policy rollout failed type={}",
                    exception.getClass().getSimpleName());
        }
    }
}
