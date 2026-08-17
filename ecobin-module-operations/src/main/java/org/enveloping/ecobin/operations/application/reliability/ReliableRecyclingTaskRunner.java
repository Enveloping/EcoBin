package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.operations.api.reliability.ReliableRecyclingTaskWorkerPort;
import org.enveloping.ecobin.operations.infrastructure.config.ReliableTaskProperties;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableFundsTaskJdbcRepository;
import org.enveloping.ecobin.recycling.api.port.ReliableRecyclingTaskExecutorPort;
import org.enveloping.ecobin.recycling.api.port.ReliableRecyclingTaskExecutorPort.Result;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;

@Service
public class ReliableRecyclingTaskRunner
        implements ReliableRecyclingTaskWorkerPort {

    private final ReliableFundsTaskJdbcRepository repository;
    private final ReliableRecyclingTaskExecutorPort executor;
    private final ReliableTaskProperties properties;

    public ReliableRecyclingTaskRunner(
            ReliableFundsTaskJdbcRepository repository,
            ReliableRecyclingTaskExecutorPort executor,
            ReliableTaskProperties properties) {
        this.repository = repository;
        this.executor = executor;
        this.properties = properties;
    }

    @Override
    public boolean runNext(String workerId) {
        ClaimedFundsTask claim = claim(workerId);
        if (claim == null) {
            return false;
        }
        long started = System.nanoTime();
        Result result;
        try {
            result = executor.execute(
                    new ReliableRecyclingTaskExecutorPort.Command(
                            claim.taskUid(),
                            claim.attemptUid(),
                            claim.attemptId(),
                            claim.taskType(),
                            claim.targetStableKey()));
        } catch (RuntimeException failure) {
            result = new Result(
                    Result.Outcome.RETRY,
                    "recycling executor raised "
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
                workerId,
                properties.getRecycling().getLeaseDuration(),
                "RECYCLING");
    }

    @Transactional(
            propagation = Propagation.REQUIRES_NEW,
            isolation = Isolation.READ_COMMITTED)
    void complete(
            ClaimedFundsTask claim,
            Result result,
            long durationMillis) {
        Duration retry = switch (result.outcome()) {
            case WAITING -> result.retryAfter() == null
                    ? properties.getRecycling().getPollInterval()
                    : result.retryAfter();
            case RETRY -> result.retryAfter() == null
                    ? properties.getRecycling().getInitialBackoff()
                    : result.retryAfter();
            default -> properties.getRecycling().getInitialBackoff();
        };
        String technical = switch (result.outcome()) {
            case DONE -> "TECHNICAL_SUCCESS";
            case WAITING -> "NO_ACTION_REQUIRED";
            case RETRY -> "RETRYABLE_FAILURE";
            case BLOCKED -> "PERMANENT_TECHNICAL_FAILURE";
        };
        repository.complete(
                claim,
                technical,
                result.diagnostic(),
                result.outcome() == Result.Outcome.DONE,
                result.outcome() == Result.Outcome.BLOCKED,
                retry,
                durationMillis,
                null,
                null);
    }

    private static long elapsed(long started) {
        return Math.max(
                0,
                (System.nanoTime() - started) / 1_000_000L);
    }
}
