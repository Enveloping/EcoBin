package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.operations.infrastructure.config.ReliableTaskProperties;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository.TaskExecution;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;

@Service
public class ReliableTaskFailureService {

    private final ReliableOperationsJdbcRepository repository;
    private final ReliableTaskProperties properties;

    public ReliableTaskFailureService(
            ReliableOperationsJdbcRepository repository,
            ReliableTaskProperties properties) {
        this.repository = repository;
        this.properties = properties;
    }

    @Transactional(
            propagation = Propagation.REQUIRES_NEW,
            isolation = Isolation.READ_COMMITTED)
    public void recordRetryableFailure(
            ClaimedInboxTask claim,
            ReliableTaskChannel channel,
            long durationMillis,
            RuntimeException failure) {
        TaskExecution execution = repository.lockTaskExecution(
                claim.taskUid(), claim.inboxUid(), claim.attemptUid());
        if (!execution.attemptLeaseToken().equals(claim.leaseToken())) {
            throw new ReliableTaskInvariantException(
                    "failure does not match the claimed attempt");
        }
        if (execution.technicalResult() != null) {
            return;
        }
        var now = repository.databaseNow();
        repository.recordAttemptResult(
                execution.attemptId(),
                "RETRYABLE_FAILURE",
                Math.max(0, durationMillis),
                safeDiagnostic(failure),
                now);

        if (!claim.leaseToken().equals(execution.currentLeaseToken())) {
            return;
        }
        if (execution.wakeVersion() != claim.claimedWakeVersion()) {
            repository.releaseForImmediateRecheck(execution.taskId(), now);
            return;
        }

        int failureCount = execution.consecutiveFailureCount() + 1;
        if (failureCount >= execution.maxAutoAttempts()) {
            repository.blockAfterRetryExhaustion(
                    execution.taskId(),
                    failureCount,
                    execution.wakeVersion(),
                    now);
            return;
        }
        ReliableTaskProperties.Channel policy = channelProperties(channel);
        Duration backoff = exponentialBackoff(
                policy.getInitialBackoff(),
                policy.getMaximumBackoff(),
                failureCount);
        repository.scheduleRetry(
                execution.taskId(), failureCount, now.plus(backoff), now);
    }

    private ReliableTaskProperties.Channel channelProperties(
            ReliableTaskChannel channel) {
        return switch (channel) {
            case IOT_DEVICE -> properties.getIotDevice();
            case FUNDS_WECHAT -> properties.getFundsWechat();
            case MAINTENANCE -> properties.getMaintenance();
        };
    }

    private static Duration exponentialBackoff(
            Duration initial, Duration maximum, int failureCount) {
        long multiplier = 1L << Math.min(30, Math.max(0, failureCount - 1));
        try {
            Duration candidate = initial.multipliedBy(multiplier);
            return candidate.compareTo(maximum) > 0 ? maximum : candidate;
        } catch (ArithmeticException overflow) {
            return maximum;
        }
    }

    private static String safeDiagnostic(RuntimeException failure) {
        String simpleName = failure.getClass().getSimpleName();
        if (simpleName.isBlank()) {
            simpleName = "RuntimeException";
        }
        return "inbox handler raised " + simpleName;
    }
}
