package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.device.api.port.ReliableDeviceCommandSubmissionPort;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmissionResult;
import org.enveloping.ecobin.operations.api.reliability.ReliableDeviceCommandWorkerPort;
import org.enveloping.ecobin.operations.api.reliability.ReliableWorkerBatchResult;
import org.slf4j.MDC;
import org.springframework.stereotype.Service;

import java.util.Optional;

@Service
public class ReliableDeviceCommandTaskRunner
        implements ReliableDeviceCommandWorkerPort {

    private final ReliableTaskClaimService claimService;
    private final ReliableTaskInFlightLimiter inFlightLimiter;
    private final ReliableDeviceCommandAttemptService attemptService;
    private final ReliableDeviceCommandSubmissionPort submissionPort;
    private final ReliableDeviceCommandCompletionService completionService;

    public ReliableDeviceCommandTaskRunner(
            ReliableTaskClaimService claimService,
            ReliableTaskInFlightLimiter inFlightLimiter,
            ReliableDeviceCommandAttemptService attemptService,
            ReliableDeviceCommandSubmissionPort submissionPort,
            ReliableDeviceCommandCompletionService completionService) {
        this.claimService = claimService;
        this.inFlightLimiter = inFlightLimiter;
        this.attemptService = attemptService;
        this.submissionPort = submissionPort;
        this.completionService = completionService;
    }

    @Override
    public ReliableWorkerBatchResult runBatch(String workerId) {
        int claimed = 0;
        int completed = 0;
        int failed = 0;
        int budget =
                claimService.batchBudget(ReliableTaskChannel.IOT_DEVICE);
        for (int index = 0; index < budget; index++) {
            if (!inFlightLimiter.tryAcquire(
                    ReliableTaskChannel.IOT_DEVICE)) {
                break;
            }
            try {
                Optional<ClaimedDeviceCommandTask> next =
                        claimService.claimNextDeviceCommand(workerId);
                if (next.isEmpty()) {
                    break;
                }
                claimed++;
                if (process(next.orElseThrow())) {
                    completed++;
                } else {
                    failed++;
                }
            } finally {
                inFlightLimiter.release(
                        ReliableTaskChannel.IOT_DEVICE);
            }
        }
        return new ReliableWorkerBatchResult(claimed, completed, failed);
    }

    private boolean process(ClaimedDeviceCommandTask claim) {
        long startedAt = System.nanoTime();
        DeviceCommandSubmissionResult result;
        try (MDC.MDCCloseable ignoredTask = MDC.putCloseable(
                     "taskUid", claim.taskUid().toString());
             MDC.MDCCloseable ignoredDevice = MDC.putCloseable(
                     "hardwareSn", claim.hardwareSn())) {
            attemptService.markExternalCallMayHaveStarted(claim);
            try {
                result = submissionPort.submit(claim.submission());
            } catch (RuntimeException failure) {
                result = new DeviceCommandSubmissionResult(
                        DeviceCommandSubmissionResult.Outcome.RETRYABLE_FAILURE,
                        null,
                        null,
                        null,
                        "DEVICE_ADAPTER_FAILURE",
                        "device submission Adapter raised "
                                + safeClassName(failure));
            }
            completionService.complete(
                    claim, result, elapsedMillis(startedAt));
        }
        return result.outcome()
                == DeviceCommandSubmissionResult.Outcome.PLATFORM_ACCEPTED;
    }

    private static long elapsedMillis(long startedAt) {
        return Math.max(
                0, (System.nanoTime() - startedAt) / 1_000_000L);
    }

    private static String safeClassName(RuntimeException failure) {
        String name = failure.getClass().getSimpleName();
        return name.isBlank() ? "RuntimeException" : name;
    }
}
