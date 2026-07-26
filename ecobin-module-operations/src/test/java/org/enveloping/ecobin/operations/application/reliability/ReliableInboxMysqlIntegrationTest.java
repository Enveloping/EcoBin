package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.framework.reliability.InboxTaskCompletion;
import org.enveloping.ecobin.framework.reliability.InboxTaskCompletionOutcome;
import org.enveloping.ecobin.framework.reliability.InboxTaskCompletionPort;
import org.enveloping.ecobin.framework.reliability.ReliableTaskWake;
import org.enveloping.ecobin.framework.reliability.ReliableTaskWakePort;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxExecutionLane;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxMessage;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxPort;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxReceipt;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxReceiptState;
import org.enveloping.ecobin.operations.infrastructure.config.ReliableTaskProperties;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.annotation.EnableTransactionManagement;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.json.JsonMapper;

import javax.sql.DataSource;
import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.time.Duration;
import java.time.LocalDateTime;
import java.util.HashSet;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

@TestInstance(TestInstance.Lifecycle.PER_CLASS)
@EnabledIfEnvironmentVariable(
        named = "ECOBIN_F08_MYSQL_URL",
        matches = "jdbc:mysql:.+")
class ReliableInboxMysqlIntegrationTest {

    private AnnotationConfigApplicationContext context;
    private DataSource dataSource;
    private JdbcTemplate jdbc;
    private TrustedInboxPort inboxPort;
    private ReliableTaskClaimService claimService;
    private ReliableTaskProperties properties;
    private ReliableInboxTaskRunner runner;
    private InboxTaskCompletionPort completionPort;
    private ReliableTaskWakePort wakePort;
    private TransactionTemplate transactionTemplate;

    @BeforeAll
    void startContext() {
        context = new AnnotationConfigApplicationContext(TestConfiguration.class);
        dataSource = context.getBean(DataSource.class);
        jdbc = context.getBean(JdbcTemplate.class);
        inboxPort = context.getBean(TrustedInboxPort.class);
        claimService = context.getBean(ReliableTaskClaimService.class);
        properties = context.getBean(ReliableTaskProperties.class);
        runner = context.getBean(ReliableInboxTaskRunner.class);
        completionPort = context.getBean(InboxTaskCompletionPort.class);
        wakePort = context.getBean(ReliableTaskWakePort.class);
        transactionTemplate = new TransactionTemplate(
                context.getBean(PlatformTransactionManager.class));
    }

    @AfterAll
    void closeContext() {
        if (context != null) {
            context.close();
        }
    }

    @BeforeEach
    void resetFixture() {
        jdbc.execute("DROP TRIGGER IF EXISTS f08_fail_task_insert");
        jdbc.execute("""
                CREATE TABLE IF NOT EXISTS f08_fake_business_effect (
                    event_key CHAR(36) CHARACTER SET ascii COLLATE ascii_bin
                        NOT NULL,
                    payload VARCHAR(255) NOT NULL,
                    created_at DATETIME(3) NOT NULL,
                    PRIMARY KEY (event_key)
                ) ENGINE=InnoDB
                """);
        jdbc.update("DELETE FROM ops_task_attempt");
        jdbc.update("DELETE FROM ops_reliable_task");
        jdbc.update("DELETE FROM ops_message_quarantine");
        jdbc.update("DELETE FROM ops_inbox_message");
        jdbc.update("DELETE FROM f08_fake_business_effect");
    }

    @Test
    void receiptTransactionIsAtomicAndDuplicateOrConflictConverges() {
        TrustedInboxMessage atomicProbe = message(
                "atomic-probe",
                "{\"value\":1}",
                "raw-before-task",
                TrustedInboxExecutionLane.DEVICE);
        jdbc.execute("""
                CREATE TRIGGER f08_fail_task_insert
                BEFORE INSERT ON ops_reliable_task
                FOR EACH ROW
                SIGNAL SQLSTATE '45000'
                    SET MESSAGE_TEXT = 'F08 forced task insert failure'
                """);
        assertThrows(DataAccessException.class, () -> inboxPort.receive(atomicProbe));
        assertEquals(0, count("ops_inbox_message"));
        assertEquals(0, count("ops_reliable_task"));
        jdbc.execute("DROP TRIGGER f08_fail_task_insert");

        AtomicReference<TrustedInboxReceipt> committedReceipt =
                new AtomicReference<>();
        assertThrows(ForcedCommitFailure.class, () ->
                transactionTemplate.executeWithoutResult(status -> {
                    committedReceipt.set(inboxPort.receive(message(
                            "stable-event",
                            "{\"b\":1.0,\"a\":\"same\"}",
                            "first raw envelope",
                            TrustedInboxExecutionLane.DEVICE)));
                    TransactionSynchronizationManager.registerSynchronization(
                            new TransactionSynchronization() {
                                @Override
                                public void beforeCommit(boolean readOnly) {
                                    throw new ForcedCommitFailure();
                                }
                            });
                }));
        TrustedInboxReceipt accepted = committedReceipt.get();
        assertNotNull(accepted);
        assertEquals(TrustedInboxReceiptState.ACCEPTED, accepted.state());
        assertTrue(accepted.transportAcknowledgementAllowed());
        assertEquals(1, count("ops_inbox_message"));
        assertEquals(1, count("ops_reliable_task"));
        String originalRawDigest = jdbc.queryForObject("""
                SELECT LOWER(HEX(raw_transport_sha256))
                FROM ops_inbox_message
                WHERE inbox_uid = ?
                """, String.class, accepted.inboxUid().toString());

        TrustedInboxReceipt duplicate = inboxPort.receive(message(
                "stable-event",
                " { \"a\" : \"same\", \"b\" : 1 } ",
                "different transport bytes",
                TrustedInboxExecutionLane.DEVICE));
        assertEquals(
                TrustedInboxReceiptState.DUPLICATE_ACCEPTED, duplicate.state());
        assertEquals(accepted.inboxUid(), duplicate.inboxUid());
        assertEquals(accepted.taskUid(), duplicate.taskUid());
        assertEquals(
                accepted.normalizedContentSha256(),
                duplicate.normalizedContentSha256());
        assertEquals(2L, jdbc.queryForObject("""
                SELECT delivery_count
                FROM ops_inbox_message
                WHERE inbox_uid = ?
                """, Long.class, accepted.inboxUid().toString()));
        assertEquals(originalRawDigest, jdbc.queryForObject("""
                SELECT LOWER(HEX(raw_transport_sha256))
                FROM ops_inbox_message
                WHERE inbox_uid = ?
                """, String.class, accepted.inboxUid().toString()));
        assertEquals("1|0", jdbc.queryForObject("""
                SELECT CONCAT(wake_version, '|', handled_wake_version)
                FROM ops_reliable_task
                WHERE task_uid = ?
                """, String.class, accepted.taskUid().toString()));

        TrustedInboxReceipt conflict = inboxPort.receive(message(
                "stable-event",
                "{\"a\":\"changed\",\"b\":1}",
                "conflicting raw envelope",
                TrustedInboxExecutionLane.DEVICE));
        assertEquals(TrustedInboxReceiptState.QUARANTINED, conflict.state());
        assertNotNull(conflict.quarantineUid());
        assertNotEquals(
                accepted.normalizedContentSha256(),
                conflict.normalizedContentSha256());
        assertEquals(1, count("ops_inbox_message"));
        assertEquals(1, count("ops_reliable_task"));
        assertEquals(1, count("ops_message_quarantine"));
        assertEquals("RECEIVED", jdbc.queryForObject("""
                SELECT processing_state
                FROM ops_inbox_message
                WHERE inbox_uid = ?
                """, String.class, accepted.inboxUid().toString()));
    }

    @Test
    void workersUseExclusiveLanesSkipLockedAndExpiredLeaseTakeover() throws Exception {
        TrustedInboxReceipt deviceOne = inboxPort.receive(message(
                "device-one", "{\"n\":1}", "d1",
                TrustedInboxExecutionLane.DEVICE));
        TrustedInboxReceipt deviceTwo = inboxPort.receive(message(
                "device-two", "{\"n\":2}", "d2",
                TrustedInboxExecutionLane.DEVICE));
        TrustedInboxReceipt funds = inboxPort.receive(message(
                "funds-one", "{\"n\":3}", "f1",
                TrustedInboxExecutionLane.FUNDS));

        ExecutorService executor = Executors.newFixedThreadPool(2);
        CountDownLatch start = new CountDownLatch(1);
        try {
            Future<ClaimedInboxTask> first = executor.submit(() -> {
                start.await();
                return claimService.claimNext(
                        ReliableTaskChannel.IOT_DEVICE, "device-worker-a")
                        .orElseThrow();
            });
            Future<ClaimedInboxTask> second = executor.submit(() -> {
                start.await();
                return claimService.claimNext(
                        ReliableTaskChannel.IOT_DEVICE, "device-worker-b")
                        .orElseThrow();
            });
            start.countDown();
            ClaimedInboxTask firstClaim = first.get();
            ClaimedInboxTask secondClaim = second.get();
            Set<UUID> claimedTaskUids = new HashSet<>();
            claimedTaskUids.add(firstClaim.taskUid());
            claimedTaskUids.add(secondClaim.taskUid());
            assertEquals(
                    Set.of(deviceOne.taskUid(), deviceTwo.taskUid()),
                    claimedTaskUids);

            ClaimedInboxTask originalClaim = firstClaim;
            assertTaskLockIsNotHeldAfterClaim(originalClaim.taskUid());

            ClaimedInboxTask fundsClaim = claimService.claimNext(
                    ReliableTaskChannel.FUNDS_WECHAT, "funds-worker-a")
                    .orElseThrow();
            assertEquals(funds.taskUid(), fundsClaim.taskUid());
            assertTrue(claimService.claimNext(
                    ReliableTaskChannel.MAINTENANCE,
                    "maintenance-worker-a").isEmpty());

            jdbc.update("""
                    UPDATE ops_reliable_task
                    SET lease_until =
                        DATE_SUB(UTC_TIMESTAMP(3), INTERVAL 1000 MICROSECOND)
                    WHERE task_uid = ?
                    """, originalClaim.taskUid().toString());
            ClaimedInboxTask takeover = claimService.claimNext(
                    ReliableTaskChannel.IOT_DEVICE, "device-worker-takeover")
                    .orElseThrow();
            assertEquals(originalClaim.taskUid(), takeover.taskUid());
            assertNotEquals(
                    originalClaim.leaseToken(), takeover.leaseToken());
            assertNotNull(jdbc.queryForObject("""
                    SELECT reclaimed_at
                    FROM ops_task_attempt
                    WHERE attempt_uid = ?
                    """, Object.class, originalClaim.attemptUid().toString()));
            assertEquals(2L, jdbc.queryForObject("""
                    SELECT attempt_sequence
                    FROM ops_reliable_task
                    WHERE task_uid = ?
                    """, Long.class, originalClaim.taskUid().toString()));

            transactionTemplate.executeWithoutResult(status ->
                    completionPort.complete(new InboxTaskCompletion(
                            originalClaim.taskUid(),
                            originalClaim.inboxUid(),
                            originalClaim.attemptUid(),
                            originalClaim.leaseToken(),
                            originalClaim.claimedWakeVersion(),
                            InboxTaskCompletionOutcome.APPLIED,
                            1)));
            assertEquals("RECEIVED", inboxState(originalClaim.inboxUid()));
            assertEquals("PENDING", taskState(originalClaim.taskUid()));
            assertEquals(
                    takeover.leaseToken().toString(),
                    jdbc.queryForObject("""
                            SELECT lease_token
                            FROM ops_reliable_task
                            WHERE task_uid = ?
                            """, String.class, originalClaim.taskUid().toString()));
            assertEquals("1|0", jdbc.queryForObject("""
                    SELECT CONCAT(wake_version, '|', handled_wake_version)
                    FROM ops_reliable_task
                    WHERE task_uid = ?
                    """, String.class, originalClaim.taskUid().toString()));
        } finally {
            executor.shutdownNow();
        }
    }

    @Test
    void businessRollbackDoesNotCompleteAndWakeVersionReusesOriginalTask() {
        TrustedInboxReceipt receipt = inboxPort.receive(message(
                "business-rollback",
                "{\"businessKey\":\"same-intent\"}",
                "business raw",
                TrustedInboxExecutionLane.DEVICE));

        ReliableBatchResult rolledBack = runner.runBatch(
                ReliableTaskChannel.IOT_DEVICE,
                "business-worker-a",
                task -> {
                    jdbc.update("""
                            INSERT INTO f08_fake_business_effect (
                                event_key, payload, created_at
                            ) VALUES (?, ?, UTC_TIMESTAMP(3))
                            """, task.inboxUid().toString(), task.normalizedPayload());
                    TransactionSynchronizationManager.registerSynchronization(
                            new TransactionSynchronization() {
                                @Override
                                public void beforeCommit(boolean readOnly) {
                                    throw new ForcedCommitFailure();
                                }
                            });
                    return InboxTaskHandlerResult.APPLIED;
                });
        assertEquals(new ReliableBatchResult(1, 0, 1), rolledBack);
        assertEquals(0, count("f08_fake_business_effect"));
        assertEquals("RECEIVED", inboxState(receipt.inboxUid()));
        assertEquals("PENDING", taskState(receipt.taskUid()));
        assertEquals("RETRYABLE_FAILURE", jdbc.queryForObject("""
                SELECT technical_result
                FROM ops_task_attempt
                WHERE task_id = (
                    SELECT id FROM ops_reliable_task WHERE task_uid = ?
                )
                """, String.class, receipt.taskUid().toString()));
        assertEquals(0L, jdbc.queryForObject("""
                SELECT COUNT(*)
                FROM ops_task_attempt
                WHERE result_recorded_at IS NULL
                """, Long.class));

        makeImmediatelyClaimable(receipt.taskUid());
        ReliableBatchResult applied = runner.runBatch(
                ReliableTaskChannel.IOT_DEVICE,
                "business-worker-b",
                task -> {
                    int inserted = jdbc.update("""
                            INSERT IGNORE INTO f08_fake_business_effect (
                                event_key, payload, created_at
                            ) VALUES (?, ?, UTC_TIMESTAMP(3))
                            """, task.inboxUid().toString(), task.normalizedPayload());
                    return inserted == 1
                            ? InboxTaskHandlerResult.APPLIED
                            : InboxTaskHandlerResult.NO_ACTION_REQUIRED;
                });
        assertEquals(new ReliableBatchResult(1, 1, 0), applied);
        assertEquals(1, count("f08_fake_business_effect"));
        assertEquals("PROCESSED", inboxState(receipt.inboxUid()));
        assertEquals("DONE", taskState(receipt.taskUid()));

        long wakeVersion = wakePort.wake(new ReliableTaskWake(
                receipt.taskUid(), "LATE_DOMAIN_FACT"));
        assertEquals(1, wakeVersion);
        assertEquals("PENDING", taskState(receipt.taskUid()));
        assertEquals(1, count("ops_reliable_task"));

        ReliableBatchResult rechecked = runner.runBatch(
                ReliableTaskChannel.IOT_DEVICE,
                "business-worker-c",
                task -> {
                    int inserted = jdbc.update("""
                            INSERT IGNORE INTO f08_fake_business_effect (
                                event_key, payload, created_at
                            ) VALUES (?, ?, UTC_TIMESTAMP(3))
                            """, task.inboxUid().toString(), task.normalizedPayload());
                    return inserted == 1
                            ? InboxTaskHandlerResult.APPLIED
                            : InboxTaskHandlerResult.NO_ACTION_REQUIRED;
                });
        assertEquals(new ReliableBatchResult(1, 1, 0), rechecked);
        assertEquals(1, count("f08_fake_business_effect"));
        assertEquals(3, count("ops_task_attempt"));
        assertEquals("DONE", taskState(receipt.taskUid()));
        assertEquals("1|1", jdbc.queryForObject("""
                SELECT CONCAT(wake_version, '|', handled_wake_version)
                FROM ops_reliable_task
                WHERE task_uid = ?
                """, String.class, receipt.taskUid().toString()));
    }

    @Test
    void queuedWorkIsClaimedOnlyWhenItCanStartWithinItsLease() {
        for (int index = 0; index < 4; index++) {
            inboxPort.receive(message(
                    "lease-queue-" + index,
                    "{\"index\":" + index + "}",
                    "lease queue " + index,
                    TrustedInboxExecutionLane.DEVICE));
        }
        ReliableTaskProperties.Channel channel = properties.getIotDevice();
        channel.setBatchSize(4);
        channel.setMaximumInFlight(4);
        channel.setBoundedQueueCapacity(4);
        channel.setLeaseDuration(Duration.ofMillis(250));
        channel.setExternalTimeout(Duration.ofMillis(200));
        AtomicBoolean startedWithExpiredLease = new AtomicBoolean();
        try {
            ReliableBatchResult result = runner.runBatch(
                    ReliableTaskChannel.IOT_DEVICE,
                    "lease-queue-worker",
                    task -> {
                        LocalDateTime databaseNow = jdbc.queryForObject(
                                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
                        if (!task.leaseUntil().isAfter(databaseNow)) {
                            startedWithExpiredLease.set(true);
                        }
                        sleepUnchecked(Duration.ofMillis(100));
                        return InboxTaskHandlerResult.NO_ACTION_REQUIRED;
                    });

            assertEquals(new ReliableBatchResult(4, 4, 0), result);
            assertFalse(
                    startedWithExpiredLease.get(),
                    "a task started after the lease acquired for queued work expired");
        } finally {
            restoreIotTestPolicy();
        }
    }

    @Test
    void maximumInFlightIsSharedAcrossConcurrentRunnerCalls() throws Exception {
        inboxPort.receive(message(
                "global-inflight-one", "{\"n\":1}", "global one",
                TrustedInboxExecutionLane.DEVICE));
        inboxPort.receive(message(
                "global-inflight-two", "{\"n\":2}", "global two",
                TrustedInboxExecutionLane.DEVICE));
        CountDownLatch firstStarted = new CountDownLatch(1);
        CountDownLatch releaseFirst = new CountDownLatch(1);
        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            Future<ReliableBatchResult> first = executor.submit(() ->
                    runner.runBatch(
                            ReliableTaskChannel.IOT_DEVICE,
                            "global-inflight-a",
                            task -> {
                                firstStarted.countDown();
                                awaitUnchecked(releaseFirst);
                                return InboxTaskHandlerResult.NO_ACTION_REQUIRED;
                            }));
            assertTrue(firstStarted.await(5, TimeUnit.SECONDS));

            ReliableBatchResult second = runner.runBatch(
                    ReliableTaskChannel.IOT_DEVICE,
                    "global-inflight-b",
                    task -> InboxTaskHandlerResult.NO_ACTION_REQUIRED);
            assertEquals(new ReliableBatchResult(0, 0, 0), second);

            releaseFirst.countDown();
            assertEquals(new ReliableBatchResult(1, 1, 0), first.get());
            assertEquals(1L, jdbc.queryForObject("""
                    SELECT COUNT(*)
                    FROM ops_reliable_task
                    WHERE state = 'PENDING'
                    """, Long.class));
        } finally {
            releaseFirst.countDown();
            executor.shutdownNow();
        }
    }

    private void assertTaskLockIsNotHeldAfterClaim(UUID taskUid) throws Exception {
        try (Connection connection = dataSource.getConnection()) {
            connection.setAutoCommit(false);
            try (PreparedStatement statement = connection.prepareStatement("""
                    SELECT id
                    FROM ops_reliable_task
                    WHERE task_uid = ?
                    FOR UPDATE NOWAIT
                    """)) {
                statement.setString(1, taskUid.toString());
                assertTrue(statement.executeQuery().next());
            } finally {
                connection.rollback();
            }
        }
    }

    private TrustedInboxMessage message(
            String externalMessageId,
            String normalizedPayload,
            String rawBody,
            TrustedInboxExecutionLane lane) {
        return new TrustedInboxMessage(
                "fake",
                "f08-reliable-tracer",
                externalMessageId,
                "FAKE_EVENT",
                1,
                rawBody.getBytes(StandardCharsets.UTF_8),
                normalizedPayload,
                "FAKE_AUTH",
                "fixture:f08",
                null,
                null,
                lane);
    }

    private int count(String table) {
        if (!table.matches("[a-z0-9_]+")) {
            throw new IllegalArgumentException("unsafe fixture table");
        }
        return jdbc.queryForObject("SELECT COUNT(*) FROM " + table, Integer.class);
    }

    private String inboxState(UUID inboxUid) {
        return jdbc.queryForObject("""
                SELECT processing_state
                FROM ops_inbox_message
                WHERE inbox_uid = ?
                """, String.class, inboxUid.toString());
    }

    private String taskState(UUID taskUid) {
        return jdbc.queryForObject("""
                SELECT state
                FROM ops_reliable_task
                WHERE task_uid = ?
                """, String.class, taskUid.toString());
    }

    private void makeImmediatelyClaimable(UUID taskUid) {
        jdbc.update("""
                UPDATE ops_reliable_task
                SET next_run_at = UTC_TIMESTAMP(3)
                WHERE task_uid = ?
                """, taskUid.toString());
    }

    private void restoreIotTestPolicy() {
        ReliableTaskProperties.Channel channel = properties.getIotDevice();
        channel.setBatchSize(1);
        channel.setMaximumInFlight(1);
        channel.setBoundedQueueCapacity(2);
        channel.setLeaseDuration(Duration.ofSeconds(30));
        channel.setExternalTimeout(Duration.ofSeconds(10));
    }

    private static void sleepUnchecked(Duration duration) {
        try {
            Thread.sleep(duration);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("test sleep interrupted", interrupted);
        }
    }

    private static void awaitUnchecked(CountDownLatch latch) {
        try {
            latch.await();
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("test latch interrupted", interrupted);
        }
    }

    private static final class ForcedCommitFailure extends RuntimeException {
    }

    @Configuration(proxyBeanMethods = false)
    @EnableTransactionManagement(proxyTargetClass = true)
    static class TestConfiguration {

        @Bean
        DataSource dataSource() {
            DriverManagerDataSource dataSource = new DriverManagerDataSource();
            dataSource.setDriverClassName("com.mysql.cj.jdbc.Driver");
            dataSource.setUrl(requiredEnvironment("ECOBIN_F08_MYSQL_URL"));
            dataSource.setUsername(requiredEnvironment("ECOBIN_F08_MYSQL_USERNAME"));
            dataSource.setPassword(requiredEnvironment("ECOBIN_F08_MYSQL_PASSWORD"));
            return dataSource;
        }

        @Bean
        JdbcTemplate jdbcTemplate(DataSource dataSource) {
            return new JdbcTemplate(dataSource);
        }

        @Bean
        PlatformTransactionManager transactionManager(DataSource dataSource) {
            return new DataSourceTransactionManager(dataSource);
        }

        @Bean
        ObjectMapper objectMapper() {
            return JsonMapper.builder().build();
        }

        @Bean
        ReliableTaskProperties reliableTaskProperties() {
            ReliableTaskProperties properties = new ReliableTaskProperties();
            properties.getIotDevice().setBatchSize(1);
            properties.getIotDevice().setMaximumInFlight(1);
            properties.getIotDevice().setBoundedQueueCapacity(2);
            properties.getIotDevice().setInitialBackoff(Duration.ofMillis(10));
            properties.getFundsWechat().setBatchSize(1);
            properties.getFundsWechat().setMaximumInFlight(1);
            properties.getFundsWechat().setBoundedQueueCapacity(2);
            properties.validate();
            return properties;
        }

        @Bean
        CanonicalJson canonicalJson(ObjectMapper objectMapper) {
            return new CanonicalJson(objectMapper);
        }

        @Bean
        ReliableOperationsJdbcRepository reliableOperationsJdbcRepository(
                JdbcTemplate jdbcTemplate) {
            return new ReliableOperationsJdbcRepository(jdbcTemplate);
        }

        @Bean
        TrustedInboxService trustedInboxService(
                ReliableOperationsJdbcRepository repository,
                CanonicalJson canonicalJson,
                ReliableTaskProperties properties) {
            return new TrustedInboxService(repository, canonicalJson, properties);
        }

        @Bean
        ReliableTaskClaimService reliableTaskClaimService(
                ReliableOperationsJdbcRepository repository,
                ReliableTaskProperties properties) {
            return new ReliableTaskClaimService(repository, properties);
        }

        @Bean
        InboxTaskCompletionService inboxTaskCompletionService(
                ReliableOperationsJdbcRepository repository) {
            return new InboxTaskCompletionService(repository);
        }

        @Bean
        ReliableTaskFailureService reliableTaskFailureService(
                ReliableOperationsJdbcRepository repository,
                ReliableTaskProperties properties) {
            return new ReliableTaskFailureService(repository, properties);
        }

        @Bean
        ReliableTaskInFlightLimiter reliableTaskInFlightLimiter(
                ReliableTaskProperties properties) {
            return new ReliableTaskInFlightLimiter(properties);
        }

        @Bean
        ReliableTaskWakeService reliableTaskWakeService(
                ReliableOperationsJdbcRepository repository) {
            return new ReliableTaskWakeService(repository);
        }

        @Bean
        ReliableInboxTaskRunner reliableInboxTaskRunner(
                ReliableTaskClaimService claimService,
                ReliableTaskInFlightLimiter inFlightLimiter,
                InboxTaskCompletionPort completionPort,
                ReliableTaskFailureService failureService,
                PlatformTransactionManager transactionManager) {
            return new ReliableInboxTaskRunner(
                    claimService,
                    inFlightLimiter,
                    completionPort,
                    failureService,
                    transactionManager);
        }

        private static String requiredEnvironment(String name) {
            String value = System.getenv(name);
            if (value == null || value.isBlank()) {
                throw new IllegalStateException(name + " is required");
            }
            return value;
        }
    }
}
