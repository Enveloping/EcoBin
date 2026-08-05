package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.framework.reliability.InboxTaskCompletion;
import org.enveloping.ecobin.framework.reliability.InboxTaskCompletionOutcome;
import org.enveloping.ecobin.framework.reliability.InboxTaskCompletionPort;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;

import java.util.Objects;
import java.util.Optional;

@Service
public class ReliableInboxTaskRunner {

    private static final Logger LOGGER =
            LoggerFactory.getLogger(ReliableInboxTaskRunner.class);

    private final ReliableTaskClaimService claimService;
    private final ReliableTaskInFlightLimiter inFlightLimiter;
    private final InboxTaskCompletionPort completionPort;
    private final ReliableTaskFailureService failureService;
    private final TransactionTemplate businessTransaction;

    public ReliableInboxTaskRunner(
            ReliableTaskClaimService claimService,
            ReliableTaskInFlightLimiter inFlightLimiter,
            InboxTaskCompletionPort completionPort,
            ReliableTaskFailureService failureService,
            PlatformTransactionManager transactionManager) {
        this.claimService = claimService;
        this.inFlightLimiter = inFlightLimiter;
        this.completionPort = completionPort;
        this.failureService = failureService;
        this.businessTransaction = new TransactionTemplate(transactionManager);
        this.businessTransaction.setIsolationLevel(
                TransactionDefinition.ISOLATION_READ_COMMITTED);
    }

    public ReliableBatchResult runBatch(
            ReliableTaskChannel channel,
            String workerId,
            InboxTaskHandler handler) {
        Objects.requireNonNull(handler, "handler");
        int claimed = 0;
        int completed = 0;
        int failed = 0;
        int batchBudget = claimService.batchBudget(channel);
        for (int index = 0; index < batchBudget; index++) {
            if (!inFlightLimiter.tryAcquire(channel)) {
                break;
            }
            try {
                Optional<ClaimedInboxTask> next =
                        claimService.claimNext(channel, workerId);
                if (next.isEmpty()) {
                    break;
                }
                ClaimedInboxTask task = next.get();
                claimed++;
                if (process(task, channel, handler)) {
                    completed++;
                } else {
                    failed++;
                }
            } finally {
                inFlightLimiter.release(channel);
            }
        }
        return new ReliableBatchResult(claimed, completed, failed);
    }

    private boolean process(
            ClaimedInboxTask claim,
            ReliableTaskChannel channel,
            InboxTaskHandler handler) {
        long startedAt = System.nanoTime();
        try (MDC.MDCCloseable ignoredTask = MDC.putCloseable(
                     "taskUid", claim.taskUid().toString());
             MDC.MDCCloseable ignoredInbox = MDC.putCloseable(
                     "inboxUid", claim.inboxUid().toString())) {
            businessTransaction.executeWithoutResult(status -> {
                InboxTaskHandlerResult result = Objects.requireNonNull(
                        handler.handle(claim), "handler result");
                long durationMillis = elapsedMillis(startedAt);
                completionPort.complete(new InboxTaskCompletion(
                        claim.taskUid(),
                        claim.inboxUid(),
                        claim.attemptUid(),
                        claim.leaseToken(),
                        claim.claimedWakeVersion(),
                        result == InboxTaskHandlerResult.APPLIED
                                ? InboxTaskCompletionOutcome.APPLIED
                                : InboxTaskCompletionOutcome.NO_ACTION_REQUIRED,
                        durationMillis));
            });
            return true;
        } catch (RuntimeException failure) {
            LOGGER.warn(
                    "Reliable inbox task failed taskUid={} inboxUid={} channel={}",
                    claim.taskUid(),
                    claim.inboxUid(),
                    channel,
                    failure);
            failureService.recordRetryableFailure(
                    claim,
                    channel,
                    elapsedMillis(startedAt),
                    failure);
            return false;
        }
    }

    private static long elapsedMillis(long startedAt) {
        return Math.max(0, (System.nanoTime() - startedAt) / 1_000_000L);
    }
}
