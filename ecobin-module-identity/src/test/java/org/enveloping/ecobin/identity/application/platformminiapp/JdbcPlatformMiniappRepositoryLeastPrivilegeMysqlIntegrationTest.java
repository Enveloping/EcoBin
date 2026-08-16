package org.enveloping.ecobin.identity.application.platformminiapp;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.transaction.support.TransactionTemplate;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;

@TestInstance(TestInstance.Lifecycle.PER_CLASS)
@EnabledIfEnvironmentVariable(
        named = "ECOBIN_H02_MYSQL_URL",
        matches = "jdbc:mysql:.+")
class JdbcPlatformMiniappRepositoryLeastPrivilegeMysqlIntegrationTest {

    private JdbcTemplate jdbc;
    private TransactionTemplate transactions;
    private JdbcPlatformMiniappRepository repository;

    @BeforeAll
    void connectAsRuntimePrincipal() {
        var dataSource = new DriverManagerDataSource(
                requiredEnvironment("ECOBIN_H02_MYSQL_URL"),
                requiredEnvironment("ECOBIN_H02_MYSQL_USERNAME"),
                requiredEnvironment("ECOBIN_H02_MYSQL_PASSWORD"));
        jdbc = new JdbcTemplate(dataSource);
        transactions = new TransactionTemplate(
                new DataSourceTransactionManager(dataSource));
        repository = new JdbcPlatformMiniappRepository(jdbc);
    }

    @Test
    void existingWechatSubjectCanBeEnsuredWithProductionRuntimeGrants() {
        assertEquals(
                List.of("lock_version"),
                jdbc.queryForList("""
                                SELECT column_name
                                FROM information_schema.column_privileges
                                WHERE table_schema = DATABASE()
                                  AND table_name = 'iam_wechat_subject'
                                  AND privilege_type = 'UPDATE'
                                ORDER BY column_name
                                """,
                        String.class));

        transactions.executeWithoutResult(status -> {
            String suffix = UUID.randomUUID().toString()
                    .replace("-", "");
            String appId = "wx" + suffix.substring(0, 16);
            String openid = "factory-existing-" + suffix;
            UUID originalSubjectUid = UUID.randomUUID();
            Instant now = Instant.now();

            jdbc.update("""
                            INSERT INTO iam_miniapp_channel (
                                channel_uid, appid, display_name,
                                login_enabled, app_secret,
                                activated_at, lock_version,
                                configured_at, created_at, updated_at
                            ) VALUES (
                                ?, ?, 'Factory least-privilege test', 1,
                                'test-app-secret', UTC_TIMESTAMP(3), 0,
                                UTC_TIMESTAMP(3), UTC_TIMESTAMP(3),
                                UTC_TIMESTAMP(3)
                            )
                            """,
                    UUID.randomUUID().toString(),
                    appId);
            long channelId = jdbc.queryForObject("""
                            SELECT id
                            FROM iam_miniapp_channel
                            WHERE appid = ?
                            """,
                    Long.class,
                    appId);

            repository.ensureWechatSubject(
                    originalSubjectUid,
                    channelId,
                    openid,
                    now);
            repository.ensureWechatSubject(
                    UUID.randomUUID(),
                    channelId,
                    openid,
                    now.plusSeconds(1));

            var subject = repository.findWechatSubject(
                    channelId,
                    openid,
                    false).orElseThrow();
            assertEquals(originalSubjectUid, subject.uid());
            status.setRollbackOnly();
        });
    }

    private static String requiredEnvironment(String name) {
        String value = System.getenv(name);
        if (value == null || value.isBlank()) {
            throw new IllegalStateException(name + " must be configured");
        }
        return value;
    }
}
