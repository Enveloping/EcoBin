package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.funds.api.port.ReliableFundsTaskExecutorPort;
import org.enveloping.ecobin.funds.api.port.ReliableFundsTaskExecutorPort.Result;
import org.enveloping.ecobin.operations.api.reliability.ReliableFundsTaskWorkerPort;
import org.enveloping.ecobin.operations.infrastructure.config.ReliableTaskProperties;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableFundsTaskJdbcRepository;
import org.springframework.dao.DataAccessException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.sql.SQLException;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

@Service
public class ReliableFundsTaskRunner implements ReliableFundsTaskWorkerPort {

    private static final Pattern CONSTRAINT_NAME = Pattern.compile(
            "(?i)(?:constraint|key)\\s+[`'\\\"]([^`'\\\"]{1,256})[`'\\\"]");

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
        } catch (DataAccessException failure) {
            DatabaseFailure databaseFailure = databaseFailure(failure);
            result = new Result(
                    databaseFailure.permanent()
                            ? Result.Outcome.BLOCKED
                            : Result.Outcome.RETRY,
                    databaseFailure.diagnostic());
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
                retry, durationMillis, result.httpStatus(),
                result.externalApiErrorCode());
    }

    private static long elapsed(long started) {
        return Math.max(0, (System.nanoTime() - started) / 1_000_000L);
    }

    private static DatabaseFailure databaseFailure(
            DataAccessException failure) {
        List<SQLException> chain = findSqlExceptions(failure);
        if (chain.isEmpty()) {
            return new DatabaseFailure(
                    false,
                    "funds executor database failure; exceptionClass="
                            + failure.getClass().getSimpleName()
                            + "; sqlState=-; vendorCode=-; constraint=-");
        }
        SQLException sql = chain.stream()
                .filter(ReliableFundsTaskRunner::isPermanent)
                .findFirst()
                .orElse(chain.getFirst());
        String sqlState = safeSqlState(sql.getSQLState());
        int vendorCode = sql.getErrorCode();
        boolean permanent = isPermanent(sql);
        return new DatabaseFailure(
                permanent,
                "funds executor database failure; exceptionClass="
                        + failure.getClass().getSimpleName()
                        + "; sqlState=" + sqlState
                        + "; vendorCode=" + vendorCode
                        + "; constraint=" + constraintName(sql));
    }

    private static List<SQLException> findSqlExceptions(Throwable failure) {
        List<SQLException> found = new ArrayList<>();
        Set<Throwable> visited = new HashSet<>();
        ArrayDeque<Throwable> remaining = new ArrayDeque<>();
        remaining.add(failure);
        while (!remaining.isEmpty()) {
            Throwable current = remaining.removeFirst();
            if (!visited.add(current)) {
                continue;
            }
            if (current instanceof SQLException sql) {
                found.add(sql);
                SQLException next = sql.getNextException();
                if (next != null) {
                    remaining.addLast(next);
                }
            }
            if (current.getCause() != null) {
                remaining.addLast(current.getCause());
            }
        }
        return found;
    }

    private static boolean isPermanent(SQLException sql) {
        String sqlState = safeSqlState(sql.getSQLState());
        return sqlState.startsWith("23") || sql.getErrorCode() == 3819;
    }

    private static String constraintName(SQLException failure) {
        SQLException current = failure;
        Set<SQLException> visited = new HashSet<>();
        while (current != null && visited.add(current)) {
            String message = current.getMessage();
            if (message != null) {
                Matcher matcher = CONSTRAINT_NAME.matcher(message);
                if (matcher.find()) {
                    String candidate = matcher.group(1);
                    if (candidate.matches("[A-Za-z0-9_.$-]{1,128}")) {
                        return candidate;
                    }
                }
            }
            current = current.getNextException();
        }
        return "-";
    }

    private static String safeSqlState(String value) {
        return value != null && value.matches("[A-Za-z0-9]{1,8}")
                ? value : "-";
    }

    private record DatabaseFailure(
            boolean permanent,
            String diagnostic) {
    }
}
