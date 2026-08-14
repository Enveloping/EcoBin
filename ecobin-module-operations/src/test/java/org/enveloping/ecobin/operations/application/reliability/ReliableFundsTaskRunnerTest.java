package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.funds.api.port.ReliableFundsTaskExecutorPort;
import org.enveloping.ecobin.operations.infrastructure.config
        .ReliableTaskProperties;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability
        .ReliableFundsTaskJdbcRepository;
import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;
import java.util.UUID;

import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ReliableFundsTaskRunnerTest {

    @Test
    void recordsWechatHttpStatusAndErrorCodeOnBlockedAttempt() {
        ReliableFundsTaskJdbcRepository repository =
                mock(ReliableFundsTaskJdbcRepository.class);
        ReliableFundsTaskExecutorPort executor =
                mock(ReliableFundsTaskExecutorPort.class);
        ReliableTaskProperties properties = new ReliableTaskProperties();
        ClaimedFundsTask claim = new ClaimedFundsTask(
                1L, UUID.randomUUID(), 2L, UUID.randomUUID(),
                UUID.randomUUID(), 0L, 0, 20,
                "CREATE_MERCHANT_TRANSFER_AUTHORIZATION",
                "AU12345678", LocalDateTime.now());
        when(repository.claimOne(
                "funds-test",
                properties.getFundsWechat().getLeaseDuration()))
                .thenReturn(claim);
        when(executor.execute(org.mockito.ArgumentMatchers.any()))
                .thenReturn(new ReliableFundsTaskExecutorPort.Result(
                        ReliableFundsTaskExecutorPort.Result.Outcome.BLOCKED,
                        "permanent channel error; message=parameter invalid",
                        null, 400, "PARAM_ERROR"));

        new ReliableFundsTaskRunner(
                repository, executor, properties).runNext("funds-test");

        verify(repository).complete(
                eq(claim), eq("PERMANENT_TECHNICAL_FAILURE"),
                eq("permanent channel error; message=parameter invalid"),
                eq(false), eq(true),
                eq(properties.getFundsWechat().getInitialBackoff()),
                anyLong(), eq(400), eq("PARAM_ERROR"));
    }
}
