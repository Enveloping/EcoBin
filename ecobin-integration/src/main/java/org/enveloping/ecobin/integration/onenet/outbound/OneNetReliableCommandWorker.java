package org.enveloping.ecobin.integration.onenet.outbound;

import org.enveloping.ecobin.operations.api.reliability.ReliableDeviceCommandWorkerPort;
import org.enveloping.ecobin.operations.api.reliability.ReliableWorkerBatchResult;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.sql.SQLException;
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
public class OneNetReliableCommandWorker {

    private static final Logger LOGGER =
            LoggerFactory.getLogger(OneNetReliableCommandWorker.class);

    private final ReliableDeviceCommandWorkerPort runner;
    private final AtomicBoolean polling = new AtomicBoolean();
    private final String workerId =
            "onenet-" + UUID.randomUUID();

    public OneNetReliableCommandWorker(
            ReliableDeviceCommandWorkerPort runner) {
        this.runner = runner;
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
                        "OneNet reliable batch claimed={} accepted={} failed={}",
                        result.claimed(),
                        result.accepted(),
                        result.failed());
            }
        } catch (RuntimeException failure) {
            FailureDiagnostic diagnostic = diagnose(failure);
            LOGGER.error(
                    "OneNet reliable worker batch failed "
                            + "failureType={} causeType={} sqlState={} "
                            + "vendorCode={}",
                    diagnostic.failureType(),
                    diagnostic.causeType(),
                    diagnostic.sqlState(),
                    diagnostic.vendorCode());
        } finally {
            polling.set(false);
        }
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
