package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.funds.api.port.ReliableFundsTaskExecutorPort;
import org.enveloping.ecobin.funds.api.port.ReliableFundsTaskExecutorPort.Result;
import org.enveloping.ecobin.operations.api.reliability.ReliableFundsTaskWorkerPort;
import org.enveloping.ecobin.operations.infrastructure.config.ReliableTaskProperties;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableFundsTaskJdbcRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;

@Service
public class ReliableFundsTaskRunner implements ReliableFundsTaskWorkerPort {

    private final ReliableFundsTaskJdbcRepository repository;
    private final ReliableFundsTaskExecutorPort executor;
    private final ReliableTaskProperties properties;

    public ReliableFundsTaskRunner(
            ReliableFundsTaskJdbcRepository repository,
            ReliableFundsTaskExecutorPort executor,
            ReliableTaskProperties properties) {
        this.repository = repository;
        this.executor = executor;
        this.properties = properties;
    }

    @Override
    public boolean runNext(String workerId) {
        ClaimedFundsTask claim = claim(workerId);
        if (claim == null) return false;
        long started = System.nanoTime();
        Result result;
        try {
            result = executor.execute(
                    new ReliableFundsTaskExecutorPort.Command(
                            claim.taskUid(), claim.attemptUid(),
                            claim.attemptId(), claim.taskType(),
                            claim.targetStableKey()));
        } catch (RuntimeException failure) {
            result = new Result(
                    Result.Outcome.RETRY,
                    "funds executor raised "
                            + failure.getClass().getSimpleName());
        }
        complete(claim, result, elapsed(started));
        return true;
    }

    @Transactional(
            propagation = Propagation.REQUIRES_NEW,
            isolation = Isolation.READ_COMMITTED)
    ClaimedFundsTask claim(String workerId) {
        return repository.claimOne(
                workerId, properties.getFundsWechat().getLeaseDuration());
    }

    @Transactional(
            propagation = Propagation.REQUIRES_NEW,
            isolation = Isolation.READ_COMMITTED)
    void complete(ClaimedFundsTask claim, Result result, long durationMillis) {
        Duration retry = switch (result.outcome()) {
            case WAITING -> result.retryAfter() == null
                    ? properties.getFundsWechat().getPollInterval()
                    : result.retryAfter();
            case RETRY -> result.retryAfter() == null
                    ? properties.getFundsWechat().getInitialBackoff()
                    : result.retryAfter();
            default -> properties.getFundsWechat().getInitialBackoff();
        };
        String technical = switch (result.outcome()) {
            case DONE -> "TECHNICAL_SUCCESS";
            case WAITING -> "NO_ACTION_REQUIRED";
            case RETRY -> "RETRYABLE_FAILURE";
            case BLOCKED -> "PERMANENT_TECHNICAL_FAILURE";
        };
        repository.complete(
                claim, technical, result.diagnostic(),
                result.outcome() == Result.Outcome.DONE,
                result.outcome() == Result.Outcome.BLOCKED,
                retry, durationMillis);
    }

    private static long elapsed(long started) {
        return Math.max(0, (System.nanoTime() - started) / 1_000_000L);
    }
}
