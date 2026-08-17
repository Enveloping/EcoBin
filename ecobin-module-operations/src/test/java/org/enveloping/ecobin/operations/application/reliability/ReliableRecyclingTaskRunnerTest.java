package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.operations.infrastructure.config
        .ReliableTaskProperties;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability
        .ReliableFundsTaskJdbcRepository;
import org.enveloping.ecobin.recycling.api.port
        .ReliableRecyclingTaskExecutorPort;
import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.UUID;

import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ReliableRecyclingTaskRunnerTest {

    @Test
    void claimsOnlyRecyclingLaneAndKeepsNotDueTaskPending() {
        ReliableFundsTaskJdbcRepository repository =
                mock(ReliableFundsTaskJdbcRepository.class);
        ReliableRecyclingTaskExecutorPort executor =
                mock(ReliableRecyclingTaskExecutorPort.class);
        ReliableTaskProperties properties = new ReliableTaskProperties();
        ClaimedFundsTask claim = claim();
        Duration remaining = Duration.ofHours(2);
        when(repository.claimOne(
                "recycling-test",
                properties.getRecycling().getLeaseDuration(),
                "RECYCLING"))
                .thenReturn(claim);
        when(executor.execute(org.mockito.ArgumentMatchers.any()))
                .thenReturn(new ReliableRecyclingTaskExecutorPort.Result(
                        ReliableRecyclingTaskExecutorPort.Result
                                .Outcome.WAITING,
                        "automatic review is not due",
                        remaining));

        new ReliableRecyclingTaskRunner(
                repository, executor, properties)
                .runNext("recycling-test");

        verify(repository).complete(
                eq(claim), eq("NO_ACTION_REQUIRED"),
                eq("automatic review is not due"),
                eq(false), eq(false), eq(remaining),
                anyLong(), eq(null), eq(null));
    }

    @Test
    void convertsUnexpectedExecutorFailureIntoRetry() {
        ReliableFundsTaskJdbcRepository repository =
                mock(ReliableFundsTaskJdbcRepository.class);
        ReliableRecyclingTaskExecutorPort executor =
                mock(ReliableRecyclingTaskExecutorPort.class);
        ReliableTaskProperties properties = new ReliableTaskProperties();
        ClaimedFundsTask claim = claim();
        when(repository.claimOne(
                "recycling-test",
                properties.getRecycling().getLeaseDuration(),
                "RECYCLING"))
                .thenReturn(claim);
        when(executor.execute(org.mockito.ArgumentMatchers.any()))
                .thenThrow(new IllegalStateException("temporary"));

        new ReliableRecyclingTaskRunner(
                repository, executor, properties)
                .runNext("recycling-test");

        verify(repository).complete(
                eq(claim), eq("RETRYABLE_FAILURE"),
                eq("recycling executor raised IllegalStateException"),
                eq(false), eq(false),
                eq(properties.getRecycling().getInitialBackoff()),
                anyLong(), eq(null), eq(null));
    }

    private static ClaimedFundsTask claim() {
        return new ClaimedFundsTask(
                1L,
                UUID.randomUUID(),
                2L,
                UUID.randomUUID(),
                UUID.randomUUID(),
                0L,
                0,
                20,
                "AUTO_REVIEW_DELIVERY_ORDER",
                "DO202608170001",
                LocalDateTime.now());
    }
}
