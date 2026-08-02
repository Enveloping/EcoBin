package org.enveloping.ecobin.integration.onenet.inbound;

import org.enveloping.ecobin.integration.onenet.OneNetDiagnosticLogger;
import org.enveloping.ecobin.operations.api.reliability.ReliableDeviceInboxWorkerPort;
import org.enveloping.ecobin.operations.api.reliability.ReliableWorkerBatchResult;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.UUID;
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
public class OneNetReliableInboxWorker {

    private static final Logger LOGGER =
            LoggerFactory.getLogger(OneNetReliableInboxWorker.class);

    private final ReliableDeviceInboxWorkerPort runner;
    private final OneNetDiagnosticLogger diagnosticLogger;
    private final AtomicBoolean polling = new AtomicBoolean();
    private final String workerId =
            "onenet-inbox-" + UUID.randomUUID();

    public OneNetReliableInboxWorker(
            ReliableDeviceInboxWorkerPort runner,
            OneNetDiagnosticLogger diagnosticLogger) {
        this.runner = runner;
        this.diagnosticLogger = diagnosticLogger;
    }

    @Scheduled(
            fixedDelayString =
                    "${ecobin.operations.reliable.iot-device.poll-interval:500ms}")
    public void poll() {
        if (!polling.compareAndSet(false, true)) {
            return;
        }
        try {
            ReliableWorkerBatchResult result = runner.runBatch(workerId);
            if (result.claimed() > 0) {
                LOGGER.info(
                        "OneNet reliable inbox claimed={} applied={} failed={}",
                        result.claimed(),
                        result.accepted(),
                        result.failed());
            }
        } catch (RuntimeException failure) {
            LOGGER.error(
                    "OneNet reliable inbox batch failed type={}",
                    failure.getClass().getSimpleName(),
                    diagnosticLogger.sanitized(failure));
        } finally {
            polling.set(false);
        }
    }
}
