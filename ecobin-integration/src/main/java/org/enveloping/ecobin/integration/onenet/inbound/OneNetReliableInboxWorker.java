package org.enveloping.ecobin.integration.onenet.inbound;

import org.enveloping.ecobin.integration.onenet.OneNetDiagnosticLogger;
import org.enveloping.ecobin.operations.api.reliability.ReliableDeviceInboxWorkerPort;
import org.enveloping.ecobin.operations.api.reliability.ReliableWorkerBatchResult;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.transaction.event.TransactionPhase;
import org.springframework.transaction.event.TransactionalEventListener;
import org.enveloping.ecobin.operations.api.reliability.ReliableWorkAvailableEvent;

import java.util.UUID;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.atomic.AtomicInteger;

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
    private final ExecutorService executor;
    private final int workerCount;
    private final AtomicInteger activeDrains = new AtomicInteger();
    private final AtomicBoolean wakeRequested = new AtomicBoolean();
    private final String workerId =
            "onenet-inbox-" + UUID.randomUUID();

    public OneNetReliableInboxWorker(
            ReliableDeviceInboxWorkerPort runner,
            OneNetDiagnosticLogger diagnosticLogger,
            @Qualifier("oneNetInboundExecutor") ExecutorService executor,
            @Value("${ecobin.operations.reliable.iot-device.worker-count:2}")
            int workerCount) {
        this.runner = runner;
        this.diagnosticLogger = diagnosticLogger;
        this.executor = executor;
        this.workerCount = workerCount;
    }

    @EventListener(ApplicationReadyEvent.class)
    public void startupDrain() {
        wake();
    }

    @TransactionalEventListener(
            phase = TransactionPhase.AFTER_COMMIT,
            fallbackExecution = true)
    public void committedWork(ReliableWorkAvailableEvent event) {
        if (event.kind()
                == ReliableWorkAvailableEvent.Kind.DEVICE_INBOX) {
            wake();
        }
    }

    @Scheduled(
            fixedDelayString =
                    "${ecobin.operations.reliable.recovery-scan-interval:30s}")
    public void recoveryScan() {
        wake();
    }

    public void wake() {
        wakeRequested.set(true);
        int submitted = 0;
        while (submitted < workerCount) {
            int current = activeDrains.get();
            if (current >= workerCount
                    || !activeDrains.compareAndSet(current, current + 1)) {
                return;
            }
            try {
                executor.execute(this::drain);
                submitted++;
            } catch (RejectedExecutionException rejected) {
                activeDrains.decrementAndGet();
                LOGGER.warn("OneNet inbound wake queue is full");
                return;
            }
        }
    }

    private void drain() {
        try {
            do {
                wakeRequested.set(false);
                ReliableWorkerBatchResult result;
                do {
                    result = runner.runBatch(workerId);
                    if (result.claimed() > 0) {
                        LOGGER.info(
                                "OneNet reliable inbox claimed={} applied={} failed={}",
                                result.claimed(),
                                result.accepted(),
                                result.failed());
                    }
                } while (result.claimed() > 0);
            } while (wakeRequested.get());
        } catch (RuntimeException failure) {
            LOGGER.error(
                    "OneNet reliable inbox batch failed type={}",
                    failure.getClass().getSimpleName(),
                    diagnosticLogger.sanitized(failure));
        } finally {
            activeDrains.decrementAndGet();
            if (wakeRequested.get()) {
                wake();
            }
        }
    }
}
