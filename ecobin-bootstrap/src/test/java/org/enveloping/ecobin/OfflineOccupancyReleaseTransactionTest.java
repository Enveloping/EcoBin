package org.enveloping.ecobin;

import org.enveloping.ecobin.device.application.delivery.OfflineDeliveryOccupancyReleaseService;
import org.enveloping.ecobin.recycling.application.clean.OfflineCleanOccupancyReleaseService;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.SingleConnectionDataSource;
import org.springframework.transaction.support.TransactionTemplate;

import java.sql.DriverManager;
import java.time.LocalDateTime;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Real MySQL transaction boundary for the three-table offline release. */
@EnabledIfEnvironmentVariable(named = "ECOBIN_MEDIAN_MYSQL_URL",
        matches = "jdbc:mysql://127\\.0\\.0\\.1:[0-9]+/ecobin_median_p1as(?:\\?.*)?")
class OfflineOccupancyReleaseTransactionTest {

    private SingleConnectionDataSource dataSource;
    private JdbcTemplate jdbc;
    private TransactionTemplate transaction;
    private String schema;

    @BeforeEach
    void setUp() throws Exception {
        var connection = DriverManager.getConnection(
                System.getenv("ECOBIN_MEDIAN_MYSQL_URL"), "root", "");
        dataSource = new SingleConnectionDataSource(connection, true);
        jdbc = new JdbcTemplate(dataSource);
        schema = "ecobin_offline_s3_"
                + UUID.randomUUID().toString().replace("-", "");
        jdbc.execute("CREATE DATABASE `" + schema + "`");
        connection.setCatalog(schema);
        transaction = new TransactionTemplate(
                new DataSourceTransactionManager(dataSource));
        createTables();
    }

    @AfterEach
    void close() {
        try {
            if (jdbc != null && schema != null) {
                assertTrue(schema.matches("ecobin_offline_s3_[0-9a-f]{32}"));
                jdbc.execute("DROP DATABASE `" + schema + "`");
            }
        } finally {
            if (dataSource != null) {
                dataSource.destroy();
            }
        }
    }

    @Test
    void deliveryReleasesOnlyAfterStrictlyMoreThanSixHundredSeconds() {
        // Leave a full one-second margin for the real clock to advance while
        // the service acquires its locks. The exact 600-second boundary is
        // covered deterministically by the pure eligibility tests.
        seedDelivery(599);
        var service = new OfflineDeliveryOccupancyReleaseService(jdbc);

        assertEquals(Boolean.FALSE, transaction.execute(status ->
                service.releaseIfEligible(11L, 1L)));
        assertNull(jdbc.queryForObject("""
                SELECT offline_occupancy_released_at
                FROM dev_delivery_session WHERE id = 11
                """, LocalDateTime.class));
        assertEquals(1, countOccupancy());

        jdbc.update("""
                UPDATE dev_device_transport_state
                SET offline_since_at = TIMESTAMPADD(
                    SECOND, -601, UTC_TIMESTAMP(3))
                WHERE asset_id = 1
                """);
        assertEquals(Boolean.TRUE, transaction.execute(status ->
                service.releaseIfEligible(11L, 1L)));
        assertNotNull(jdbc.queryForObject("""
                SELECT offline_occupancy_released_at
                FROM dev_delivery_session WHERE id = 11
                """, LocalDateTime.class));
        assertEquals("IN_PROGRESS", jdbc.queryForObject(
                "SELECT status FROM dev_delivery_session WHERE id = 11",
                String.class));
        assertEquals(0, countOccupancy());
        assertEquals(Boolean.FALSE, transaction.execute(status ->
                service.releaseIfEligible(11L, 1L)));
    }

    @Test
    void cleanMarkAndDeleteRollbackTogetherWhenDeleteFails() {
        seedClean(601);
        jdbc.execute("""
                CREATE TRIGGER fail_offline_slot_delete
                BEFORE DELETE ON dev_device_occupancy
                FOR EACH ROW
                SIGNAL SQLSTATE '45000'
                    SET MESSAGE_TEXT = 'injected offline release failure'
                """);
        var service = new OfflineCleanOccupancyReleaseService(jdbc);

        assertThrows(RuntimeException.class, () ->
                transaction.executeWithoutResult(status ->
                        service.releaseIfEligible(21L, 1L)));

        assertNull(jdbc.queryForObject("""
                SELECT offline_occupancy_released_at
                FROM rec_clean_operation WHERE id = 21
                """, LocalDateTime.class));
        assertEquals("IN_PROGRESS", jdbc.queryForObject(
                "SELECT status FROM rec_clean_operation WHERE id = 21",
                String.class));
        assertEquals(1, countOccupancy());
    }

    @Test
    void cleanReleaseDoesNotEndOriginalOperation() {
        seedClean(601);
        var service = new OfflineCleanOccupancyReleaseService(jdbc);

        assertEquals(Boolean.TRUE, transaction.execute(status ->
                service.releaseIfEligible(21L, 1L)));

        assertNotNull(jdbc.queryForObject("""
                SELECT offline_occupancy_released_at
                FROM rec_clean_operation WHERE id = 21
                """, LocalDateTime.class));
        assertEquals("IN_PROGRESS", jdbc.queryForObject(
                "SELECT status FROM rec_clean_operation WHERE id = 21",
                String.class));
        assertEquals(0, countOccupancy());
    }

    private void createTables() {
        jdbc.execute("""
                CREATE TABLE dev_device_asset (
                    id BIGINT PRIMARY KEY
                ) ENGINE=InnoDB
                """);
        jdbc.execute("INSERT INTO dev_device_asset VALUES (1)");
        jdbc.execute("""
                CREATE TABLE dev_device_transport_state (
                    asset_id BIGINT PRIMARY KEY,
                    onenet_connection_status VARCHAR(16) NOT NULL,
                    offline_since_at DATETIME(3) NULL
                ) ENGINE=InnoDB
                """);
        jdbc.execute("""
                CREATE TABLE dev_delivery_session (
                    id BIGINT PRIMARY KEY,
                    tenant_id BIGINT NOT NULL,
                    organization_id BIGINT NOT NULL,
                    asset_id BIGINT NOT NULL,
                    status VARCHAR(32) NOT NULL,
                    ended_at DATETIME(3) NULL,
                    offline_occupancy_released_at DATETIME(3) NULL,
                    lock_version BIGINT NOT NULL DEFAULT 0,
                    updated_at DATETIME(3) NULL
                ) ENGINE=InnoDB
                """);
        jdbc.execute("""
                CREATE TABLE rec_clean_operation (
                    id BIGINT PRIMARY KEY,
                    tenant_id BIGINT NOT NULL,
                    organization_id BIGINT NOT NULL,
                    asset_id BIGINT NOT NULL,
                    status VARCHAR(32) NOT NULL,
                    ended_at DATETIME(3) NULL,
                    offline_occupancy_released_at DATETIME(3) NULL,
                    lock_version BIGINT NOT NULL DEFAULT 0,
                    updated_at DATETIME(3) NULL
                ) ENGINE=InnoDB
                """);
        jdbc.execute("""
                CREATE TABLE dev_device_occupancy (
                    asset_id BIGINT PRIMARY KEY,
                    tenant_id BIGINT NOT NULL,
                    organization_id BIGINT NOT NULL,
                    occupancy_kind VARCHAR(16) NOT NULL,
                    delivery_session_id BIGINT NULL,
                    clean_operation_id BIGINT NULL
                ) ENGINE=InnoDB
                """);
    }

    private void seedDelivery(long offlineSeconds) {
        seedTransport(offlineSeconds);
        jdbc.update("""
                INSERT INTO dev_delivery_session (
                    id, tenant_id, organization_id, asset_id, status)
                VALUES (11, 2, 3, 1, 'IN_PROGRESS')
                """);
        jdbc.update("""
                INSERT INTO dev_device_occupancy (
                    asset_id, tenant_id, organization_id,
                    occupancy_kind, delivery_session_id)
                VALUES (1, 2, 3, 'DELIVERY', 11)
                """);
    }

    private void seedClean(long offlineSeconds) {
        seedTransport(offlineSeconds);
        jdbc.update("""
                INSERT INTO rec_clean_operation (
                    id, tenant_id, organization_id, asset_id, status)
                VALUES (21, 2, 3, 1, 'IN_PROGRESS')
                """);
        jdbc.update("""
                INSERT INTO dev_device_occupancy (
                    asset_id, tenant_id, organization_id,
                    occupancy_kind, clean_operation_id)
                VALUES (1, 2, 3, 'CLEAN', 21)
                """);
    }

    private void seedTransport(long offlineSeconds) {
        jdbc.update("""
                INSERT INTO dev_device_transport_state (
                    asset_id, onenet_connection_status, offline_since_at)
                VALUES (
                    1, 'OFFLINE',
                    TIMESTAMPADD(SECOND, ?, UTC_TIMESTAMP(3)))
                """, -offlineSeconds);
    }

    private int countOccupancy() {
        return jdbc.queryForObject(
                "SELECT COUNT(*) FROM dev_device_occupancy",
                Integer.class);
    }
}
