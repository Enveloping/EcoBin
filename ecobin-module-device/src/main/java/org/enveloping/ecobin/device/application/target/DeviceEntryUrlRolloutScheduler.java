package org.enveloping.ecobin.device.application.target;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/** Resumes the current base-URL rollout in bounded database transactions. */
@Component
public class DeviceEntryUrlRolloutScheduler {

    private static final Logger LOGGER = LoggerFactory.getLogger(
            DeviceEntryUrlRolloutScheduler.class);

    private final DeviceEntryUrlRolloutService service;

    public DeviceEntryUrlRolloutScheduler(
            DeviceEntryUrlRolloutService service) {
        this.service = service;
    }

    @Scheduled(
            initialDelayString =
                    "${ecobin.device.entry-url-rollout-initial-delay-ms:1000}",
            fixedDelayString =
                    "${ecobin.device.entry-url-rollout-delay-ms:5000}")
    public void reconcile() {
        try {
            service.reconcileNextBatch();
        } catch (RuntimeException exception) {
            LOGGER.warn(
                    "device entry URL rollout batch failed type={}",
                    exception.getClass().getSimpleName());
        }
    }
}
