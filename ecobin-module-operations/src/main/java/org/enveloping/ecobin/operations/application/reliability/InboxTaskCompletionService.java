package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.framework.reliability.InboxTaskCompletion;
import org.enveloping.ecobin.framework.reliability.InboxTaskCompletionOutcome;
import org.enveloping.ecobin.framework.reliability.InboxTaskCompletionPort;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository.TaskExecution;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

@Service
public class InboxTaskCompletionService implements InboxTaskCompletionPort {

    private final ReliableOperationsJdbcRepository repository;

    public InboxTaskCompletionService(
            ReliableOperationsJdbcRepository repository) {
        this.repository = repository;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public void complete(InboxTaskCompletion completion) {
        TaskExecution execution = repository.lockTaskExecution(
                completion.taskUid(),
                completion.inboxUid(),
                completion.attemptUid());
        if (!execution.attemptLeaseToken().equals(completion.leaseToken())
                || execution.claimedWakeVersion()
                        != completion.claimedWakeVersion()) {
            throw new ReliableTaskInvariantException(
                    "completion does not match the claimed attempt");
        }
        String technicalResult = completion.outcome()
                == InboxTaskCompletionOutcome.APPLIED
                ? "TECHNICAL_SUCCESS"
                : "NO_ACTION_REQUIRED";
        if (execution.technicalResult() != null) {
            if (!technicalResult.equals(execution.technicalResult())) {
                throw new ReliableTaskInvariantException(
                        "attempt already carries a different result");
            }
            if ("DONE".equals(execution.state())
                    && "PROCESSED".equals(execution.inboxState())) {
                return;
            }
        } else {
            repository.recordAttemptResult(
                    execution.attemptId(),
                    technicalResult,
                    completion.durationMillis(),
                    null,
                    repository.databaseNow());
        }

        var now = repository.databaseNow();
        boolean ownsLease = completion.leaseToken()
                .equals(execution.currentLeaseToken());
        if (ownsLease
                && execution.wakeVersion() == completion.claimedWakeVersion()) {
            repository.markInboxProcessed(execution.inboxId(), now);
            repository.markTaskDone(
                    execution.taskId(), execution.wakeVersion(), now);
            return;
        }
        if (ownsLease) {
            repository.releaseForImmediateRecheck(execution.taskId(), now);
        } else {
            repository.incrementWakeForLateResult(execution.taskId(), now);
        }
    }
}
