package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.device.api.result.DeviceCommandSubmissionResult;
import org.enveloping.ecobin.operations.infrastructure.config.ReliableTaskProperties;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository.DeviceTaskExecution;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;

@Service
public class ReliableDeviceCommandCompletionService {

    private final ReliableOperationsJdbcRepository repository;
    private final ReliableTaskProperties properties;

    public ReliableDeviceCommandCompletionService(
            ReliableOperationsJdbcRepository repository,
            ReliableTaskProperties properties) {
        this.repository = repository;
        this.properties = properties;
    }

    @Transactional(
            propagation = Propagation.REQUIRES_NEW,
            isolation = Isolation.READ_COMMITTED)
    public void complete(
            ClaimedDeviceCommandTask claim,
            DeviceCommandSubmissionResult result,
            long durationMillis) {
        DeviceTaskExecution execution =
                repository.lockDeviceTaskExecution(
                        claim.taskUid(),
                        claim.commandUid(),
                        claim.attemptUid());
        if (!execution.attemptLeaseToken().equals(claim.leaseToken())
                || execution.claimedWakeVersion()
                        != claim.claimedWakeVersion()) {
            throw new ReliableTaskInvariantException(
                    "device completion does not match the claimed attempt");
        }
        String technicalResult = technicalResult(result.outcome());
        if (execution.technicalResult() == null) {
            repository.recordDeviceAttemptResult(
                    execution.attemptId(),
                    technicalResult,
                    durationMillis,
                    result.requestSha256(),
                    result.responseSha256(),
                    result.httpStatus(),
                    result.externalErrorCode(),
                    result.redactedDiagnostic(),
                    repository.databaseNow());
        } else if (!technicalResult.equals(execution.technicalResult())) {
            throw new ReliableTaskInvariantException(
                    "device attempt already carries a different result");
        }

        if (!"PENDING".equals(execution.state())) {
            return;
        }
        boolean ownsLease = claim.leaseToken()
                .equals(execution.currentLeaseToken());
        if (!ownsLease) {
            return;
        }
        var now = repository.databaseNow();
        if (execution.wakeVersion() != claim.claimedWakeVersion()) {
            repository.releaseForImmediateRecheck(execution.taskId(), now);
            return;
        }

        if (result.outcome()
                == DeviceCommandSubmissionResult.Outcome.PERMANENT_FAILURE) {
            repository.blockDeviceTask(
                    execution.taskId(),
                    execution.consecutiveFailureCount() + 1,
                    execution.wakeVersion(),
                    "PERMANENT_TECHNICAL_FAILURE",
                    "frozen device command was permanently rejected by transport",
                    now);
            return;
        }
        if (execution.attemptsForCurrentWake()
                >= execution.maxAutoAttempts()) {
            String reason = result.outcome()
                    == DeviceCommandSubmissionResult.Outcome.PLATFORM_ACCEPTED
                    ? "DEVICE_CONFIRMATION_TIMEOUT"
                    : "AUTO_RETRY_EXHAUSTED";
            String diagnostic = result.outcome()
                    == DeviceCommandSubmissionResult.Outcome.PLATFORM_ACCEPTED
                    ? "platform accepted submissions but no trusted device proof arrived"
                    : "automatic device submission retry limit reached";
            repository.blockDeviceTask(
                    execution.taskId(),
                    execution.consecutiveFailureCount()
                            + (result.outcome()
                            == DeviceCommandSubmissionResult.Outcome
                                    .RETRYABLE_FAILURE ? 1 : 0),
                    execution.wakeVersion(),
                    reason,
                    diagnostic,
                    now);
            return;
        }

        Duration backoff = exponentialBackoff(
                properties.getIotDevice().getInitialBackoff(),
                properties.getIotDevice().getMaximumBackoff(),
                execution.attemptsForCurrentWake());
        if (result.outcome()
                == DeviceCommandSubmissionResult.Outcome.PLATFORM_ACCEPTED) {
            repository.scheduleAwaitingDeviceEvidence(
                    execution.taskId(), now.plus(backoff), now);
        } else {
            repository.scheduleRetry(
                    execution.taskId(),
                    execution.consecutiveFailureCount() + 1,
                    now.plus(backoff),
                    now);
        }
    }

    private static String technicalResult(
            DeviceCommandSubmissionResult.Outcome outcome) {
        return switch (outcome) {
            case PLATFORM_ACCEPTED -> "TECHNICAL_SUCCESS";
            case RETRYABLE_FAILURE -> "RETRYABLE_FAILURE";
            case PERMANENT_FAILURE -> "PERMANENT_TECHNICAL_FAILURE";
        };
    }

    private static Duration exponentialBackoff(
            Duration initial, Duration maximum, int attemptCount) {
        long multiplier =
                1L << Math.min(30, Math.max(0, attemptCount - 1));
        try {
            Duration candidate = initial.multipliedBy(multiplier);
            return candidate.compareTo(maximum) > 0
                    ? maximum
                    : candidate;
        } catch (ArithmeticException overflow) {
            return maximum;
        }
    }
}
