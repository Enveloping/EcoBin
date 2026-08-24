package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.funds.api.port.ReliableFundsTaskExecutorPort;
import org.enveloping.ecobin.operations.infrastructure.config
        .ReliableTaskProperties;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability
        .ReliableFundsTaskJdbcRepository;
import org.junit.jupiter.api.Test;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.dao.DataIntegrityViolationException;

import java.sql.SQLException;
import java.time.LocalDateTime;
import java.util.UUID;

import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ReliableFundsTaskRunnerTest {

    @Test
    void keepsTransientDatabaseFailureRetryable() {
        ReliableFundsTaskJdbcRepository repository =
                mock(ReliableFundsTaskJdbcRepository.class);
        ReliableFundsTaskExecutorPort executor =
                mock(ReliableFundsTaskExecutorPort.class);
        ReliableTaskProperties properties = new ReliableTaskProperties();
        ClaimedFundsTask claim = new ClaimedFundsTask(
                1L, UUID.randomUUID(), 2L, UUID.randomUUID(),
                UUID.randomUUID(), 0L, 0, 20,
                "SUBMIT_MERCHANT_TRANSFER", "WD12345678",
                LocalDateTime.now());
        when(repository.claimOne(
                "funds-test",
                properties.getFundsWechat().getLeaseDuration()))
                .thenReturn(claim);
        when(executor.execute(org.mockito.ArgumentMatchers.any()))
                .thenThrow(new DataAccessResourceFailureException(
                        "connection failed",
                        new SQLException("connection reset", "08006", 0)));

        new ReliableFundsTaskRunner(
                repository, executor, properties).runNext("funds-test");

        verify(repository).complete(
                eq(claim), eq("RETRYABLE_FAILURE"),
                eq("funds executor database failure; "
                        + "exceptionClass=DataAccessResourceFailureException; "
                        + "sqlState=08006; vendorCode=0; constraint=-"),
                eq(false), eq(false),
                eq(properties.getFundsWechat().getInitialBackoff()),
                anyLong(), eq(null), eq(null));
    }

    @Test
    void blocksPermanentDatabaseConstraintFailureWithoutRetrying() {
        ReliableFundsTaskJdbcRepository repository =
                mock(ReliableFundsTaskJdbcRepository.class);
        ReliableFundsTaskExecutorPort executor =
                mock(ReliableFundsTaskExecutorPort.class);
        ReliableTaskProperties properties = new ReliableTaskProperties();
        ClaimedFundsTask claim = new ClaimedFundsTask(
                1L, UUID.randomUUID(), 2L, UUID.randomUUID(),
                UUID.randomUUID(), 0L, 0, 500,
                "SUBMIT_MERCHANT_TRANSFER",
                "AW20000000000040008000000000000001",
                LocalDateTime.now());
        when(repository.claimOne(
                "funds-test",
                properties.getFundsWechat().getLeaseDuration()))
                .thenReturn(claim);
        when(executor.execute(org.mockito.ArgumentMatchers.any()))
                .thenThrow(new DataIntegrityViolationException(
                        "database constraint rejected local transfer",
                        new SQLException(
                                "Check constraint "
                                        + "'ck_fund_transfer_authorization_times' "
                                        + "is violated.",
                                "HY000", 3819)));

        new ReliableFundsTaskRunner(
                repository, executor, properties).runNext("funds-test");

        verify(repository).complete(
                eq(claim), eq("PERMANENT_TECHNICAL_FAILURE"),
                eq("funds executor database failure; "
                        + "exceptionClass=DataIntegrityViolationException; "
                        + "sqlState=HY000; vendorCode=3819; "
                        + "constraint=ck_fund_transfer_authorization_times"),
                eq(false), eq(true),
                eq(properties.getFundsWechat().getInitialBackoff()),
                anyLong(), eq(null), eq(null));
    }

    @Test
    void findsPermanentConstraintFailureBehindTransientSqlWrapper() {
        ReliableFundsTaskJdbcRepository repository =
                mock(ReliableFundsTaskJdbcRepository.class);
        ReliableFundsTaskExecutorPort executor =
                mock(ReliableFundsTaskExecutorPort.class);
        ReliableTaskProperties properties = new ReliableTaskProperties();
        ClaimedFundsTask claim = new ClaimedFundsTask(
                1L, UUID.randomUUID(), 2L, UUID.randomUUID(),
                UUID.randomUUID(), 0L, 0, 500,
                "CREATE_MERCHANT_TRANSFER_AUTHORIZATION",
                "AW20000000000040008000000000000001",
                LocalDateTime.now());
        when(repository.claimOne(
                "funds-test",
                properties.getFundsWechat().getLeaseDuration()))
                .thenReturn(claim);
        SQLException wrapper = new SQLException(
                "statement execution failed", "HY000", 0);
        wrapper.setNextException(new SQLException(
                "Check constraint "
                        + "'ck_fund_transfer_authorization_times' "
                        + "is violated.",
                "23000", 3819));
        when(executor.execute(org.mockito.ArgumentMatchers.any()))
                .thenThrow(new DataIntegrityViolationException(
                        "wrapped database failure", wrapper));

        new ReliableFundsTaskRunner(
                repository, executor, properties).runNext("funds-test");

        verify(repository).complete(
                eq(claim), eq("PERMANENT_TECHNICAL_FAILURE"),
                eq("funds executor database failure; "
                        + "exceptionClass=DataIntegrityViolationException; "
                        + "sqlState=23000; vendorCode=3819; "
                        + "constraint=ck_fund_transfer_authorization_times"),
                eq(false), eq(true),
                eq(properties.getFundsWechat().getInitialBackoff()),
                anyLong(), eq(null), eq(null));
    }

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
