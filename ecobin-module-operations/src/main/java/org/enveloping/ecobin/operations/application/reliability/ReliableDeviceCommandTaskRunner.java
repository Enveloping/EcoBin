package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.device.api.port.ReliableDeviceCommandSubmissionPort;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmissionResult;
import org.enveloping.ecobin.operations.api.reliability.ReliableDeviceCommandWorkerPort;
import org.enveloping.ecobin.operations.api.reliability.ReliableWorkerBatchResult;
import org.slf4j.MDC;
import org.springframework.stereotype.Service;

import java.util.Optional;

/**
 * 从可靠任务表认领设备命令并调用外部下发适配器。
 *
 * <p>业务事务只负责登记任务，真实 OneNet HTTP 调用发生在事务外。每次尝试先记录
 * “外部调用可能已经开始”，即使进程在请求途中崩溃，也不会把未知结果误当成从未发送。</p>
 */
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
        int batchBudget = claimService.batchBudget(
                ReliableTaskChannel.IOT_DEVICE);
        for (int index = 0; index < batchBudget; index++) {
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
                ClaimedDeviceCommandTask task = next.get();
                claimed++;
                if (process(task)) {
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

    @Override
    public int recoverExpiredEvidenceWaits() {
        return completionService.expireEvidenceWaits();
    }

    private boolean process(ClaimedDeviceCommandTask claim) {
        long startedAt = System.nanoTime();
        DeviceCommandSubmissionResult result;
        try (MDC.MDCCloseable ignoredTask = MDC.putCloseable(
                     "taskUid", claim.taskUid().toString());
             MDC.MDCCloseable ignoredDevice = MDC.putCloseable(
                     "hardwareSn", claim.hardwareSn())) {
            // 这是外部不可逆边界的本地证据，必须早于网络调用落库。
            if (attemptService.prepareExternalCall(claim)
                    == ReliableDeviceCommandAttemptService
                            .DispatchPreparation.NO_SUBMISSION) {
                return true;
            }
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
            // completionService 按平台受理、离线、身份不存在、临时失败和永久失败分别收敛；
            // 平台受理后的物理命令会等待设备证据，不在这里宣告投递完成。
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
