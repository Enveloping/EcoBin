package org.enveloping.ecobin.integration.onenet.inbound;

import org.enveloping.ecobin.operations.api.reliability.DeviceTelemetryRetentionPort;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.time.Instant;

@Component
@ConditionalOnProperty(
        prefix = "ecobin.external",
        name = "mode",
        havingValue = "real")
public class OneNetTelemetryRetentionWorker {

    private static final Logger LOGGER =
            LoggerFactory.getLogger(OneNetTelemetryRetentionWorker.class);

    private final DeviceTelemetryRetentionPort retention;
    private final Duration retainedFor;
    private final int batchSize;
    private final int maximumBatches;

    public OneNetTelemetryRetentionWorker(
            DeviceTelemetryRetentionPort retention,
            @Value("${ecobin.operations.telemetry.retained-for:24h}")
            Duration retainedFor,
            @Value("${ecobin.operations.telemetry.cleanup-batch-size:1000}")
            int batchSize,
            @Value("${ecobin.operations.telemetry.maximum-batches:20}")
            int maximumBatches) {
        this.retention = retention;
        this.retainedFor = retainedFor;
        this.batchSize = batchSize;
        this.maximumBatches = maximumBatches;
    }

    @Scheduled(
            fixedDelayString =
                    "${ecobin.operations.telemetry.cleanup-interval:1h}")
    public void purge() {
        int total = 0;
        Instant cutoff = Instant.now().minus(retainedFor);
        for (int batch = 0; batch < maximumBatches; batch++) {
            int deleted = retention.purgeRuntimeSnapshotsBefore(
                    cutoff, batchSize);
            total += deleted;
            if (deleted < batchSize) {
                break;
            }
        }
        if (total > 0) {
            LOGGER.info(
                    "OneNet runtime telemetry retention deleted={}",
                    total);
        }
    }
}
