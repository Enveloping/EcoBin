package org.enveloping.ecobin.integration.onenet.outbound;

import org.enveloping.ecobin.integration.onenet.OneNetDiagnosticLogger;
import org.enveloping.ecobin.operations.api.reliability.ReliableDeviceCommandWorkerPort;
import org.enveloping.ecobin.operations.api.reliability.ReliableWorkerBatchResult;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
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

import java.sql.SQLException;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.Executor;
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
public class OneNetReliableCommandWorker {

    private static final Logger LOGGER =
            LoggerFactory.getLogger(OneNetReliableCommandWorker.class);

    private final ReliableDeviceCommandWorkerPort runner;
    private final OneNetDiagnosticLogger diagnosticLogger;
    private final Executor executor;
    private final int workerCount;
    private final AtomicInteger activeDrains = new AtomicInteger();
    private final AtomicBoolean wakeRequested = new AtomicBoolean();
    private final String workerId =
            "onenet-" + UUID.randomUUID();

    @Autowired
    public OneNetReliableCommandWorker(
            ReliableDeviceCommandWorkerPort runner,
            OneNetDiagnosticLogger diagnosticLogger,
            @Qualifier("oneNetOutboundExecutor") Executor executor,
            @Value("${ecobin.operations.reliable.iot-device.worker-count:2}")
            int workerCount) {
        this.runner = runner;
        this.diagnosticLogger = diagnosticLogger;
        this.executor = executor;
        this.workerCount = workerCount;
    }

    OneNetReliableCommandWorker(
            ReliableDeviceCommandWorkerPort runner,
            OneNetDiagnosticLogger diagnosticLogger) {
        this(runner, diagnosticLogger, Runnable::run, 1);
    }

    /** Direct, synchronous single drain used by focused worker diagnostics. */
    void poll() {
        activeDrains.incrementAndGet();
        drain();
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
                == ReliableWorkAvailableEvent.Kind.DEVICE_COMMAND) {
            wake();
        }
    }

    @Scheduled(
            fixedDelayString =
                    "${ecobin.operations.reliable.recovery-scan-interval:30s}")
    public void recoveryScan() {
        try {
            int expired = runner.recoverExpiredEvidenceWaits();
            if (expired > 0) {
                LOGGER.warn(
                        "OneNet device evidence waits expired={}", expired);
            }
            wake();
        } catch (RuntimeException failure) {
            logFailure(failure);
        }
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
                LOGGER.warn("OneNet outbound wake queue is full");
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
                                "OneNet reliable batch claimed={} accepted={} failed={}",
                                result.claimed(),
                                result.accepted(),
                                result.failed());
                    }
                } while (result.claimed() > 0);
            } while (wakeRequested.get());
        } catch (RuntimeException failure) {
            logFailure(failure);
        } finally {
            activeDrains.decrementAndGet();
            if (wakeRequested.get()) {
                wake();
            }
        }
    }

    private void logFailure(RuntimeException failure) {
            FailureDiagnostic diagnostic = diagnose(failure);
            LOGGER.error(
                    "OneNet reliable worker batch failed "
                            + "failureType={} causeType={} sqlState={} "
                            + "vendorCode={}",
                    diagnostic.failureType(),
                    diagnostic.causeType(),
                    diagnostic.sqlState(),
                    diagnostic.vendorCode(),
                    diagnosticLogger.sanitized(failure));
    }

    private static FailureDiagnostic diagnose(RuntimeException failure) {
        Throwable cursor = failure;
        Throwable deepest = failure;
        SQLException sqlFailure = null;
        for (int depth = 0; cursor != null && depth < 32; depth++) {
            deepest = cursor;
            if (cursor instanceof SQLException candidate) {
                sqlFailure = candidate;
            }
            cursor = cursor.getCause();
        }
        return new FailureDiagnostic(
                failure.getClass().getSimpleName(),
                deepest.getClass().getSimpleName(),
                sqlFailure == null || sqlFailure.getSQLState() == null
                        ? "NONE"
                        : sqlFailure.getSQLState(),
                sqlFailure == null
                        ? "NONE"
                        : Integer.toString(sqlFailure.getErrorCode()));
    }

    private record FailureDiagnostic(
            String failureType,
            String causeType,
            String sqlState,
            String vendorCode) {
    }
}
