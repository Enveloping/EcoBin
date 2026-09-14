package org.enveloping.ecobin.device.application.delivery;

import org.h2.jdbcx.JdbcDataSource;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class DeliveryCommandObservationReconciliationSchedulerTest {

    @Test
    void doesNotReplayFactsAlreadyProjectedWithBackendReceiptTime() {
        JdbcDataSource source = new JdbcDataSource();
        source.setURL("jdbc:h2:mem:delivery-reconciliation-"
                + System.nanoTime()
                + ";MODE=MySQL;DATABASE_TO_LOWER=TRUE;DB_CLOSE_DELAY=-1");
        JdbcTemplate jdbc = new JdbcTemplate(source);
        jdbc.execute("""
                CREATE TABLE dev_delivery_session (
                    id BIGINT PRIMARY KEY,
                    tenant_id BIGINT NOT NULL,
                    organization_id BIGINT NOT NULL,
                    asset_id BIGINT NOT NULL,
                    status VARCHAR(40) NOT NULL,
                    first_edge_accepted_at TIMESTAMP NULL,
                    first_physical_progress_at TIMESTAMP NULL,
                    created_at TIMESTAMP NOT NULL
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_device_command (
                    id BIGINT PRIMARY KEY,
                    tenant_id BIGINT NOT NULL,
                    organization_id BIGINT NOT NULL,
                    asset_id BIGINT NOT NULL,
                    delivery_session_id BIGINT NULL,
                    command_type VARCHAR(64) NOT NULL
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_edge_event (
                    id BIGINT PRIMARY KEY,
                    tenant_id BIGINT NOT NULL,
                    organization_id BIGINT NOT NULL,
                    asset_id BIGINT NOT NULL,
                    edge_event_sequence BIGINT NOT NULL,
                    device_occurred_at TIMESTAMP NULL,
                    backend_received_at TIMESTAMP NOT NULL
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_device_command_event (
                    id BIGINT PRIMARY KEY,
                    tenant_id BIGINT NOT NULL,
                    organization_id BIGINT NOT NULL,
                    asset_id BIGINT NOT NULL,
                    command_id BIGINT NOT NULL,
                    edge_event_id BIGINT NOT NULL,
                    observation_stage VARCHAR(40) NOT NULL,
                    error_code VARCHAR(64) NULL
                )
                """);

        LocalDateTime createdAt = LocalDateTime.of(
                2026, 9, 9, 13, 46, 20);
        LocalDateTime acceptedDeviceAt = createdAt.plusSeconds(1);
        LocalDateTime acceptedReceivedAt = createdAt.plusSeconds(2);
        LocalDateTime mcuDeviceAt = createdAt.plusSeconds(8);
        LocalDateTime mcuReceivedAt = createdAt.plusSeconds(9);
        jdbc.update("""
                        INSERT INTO dev_delivery_session (
                            id, tenant_id, organization_id, asset_id, status,
                            first_edge_accepted_at,
                            first_physical_progress_at, created_at
                        ) VALUES (10, 20, 30, 40, 'IN_PROGRESS', ?, ?, ?)
                        """,
                acceptedReceivedAt, mcuReceivedAt, createdAt);
        jdbc.update("""
                INSERT INTO dev_device_command (
                    id, tenant_id, organization_id, asset_id,
                    delivery_session_id, command_type
                ) VALUES (
                    50, 20, 30, 40, 10, 'START_DELIVERY_SESSION'
                )
                """);
        jdbc.update("""
                        INSERT INTO dev_edge_event (
                            id, tenant_id, organization_id, asset_id,
                            edge_event_sequence, device_occurred_at,
                            backend_received_at
                        ) VALUES (60, 20, 30, 40, 110, ?, ?)
                        """,
                acceptedDeviceAt, acceptedReceivedAt);
        jdbc.update("""
                        INSERT INTO dev_edge_event (
                            id, tenant_id, organization_id, asset_id,
                            edge_event_sequence, device_occurred_at,
                            backend_received_at
                        ) VALUES (61, 20, 30, 40, 113, ?, ?)
                        """,
                mcuDeviceAt, mcuReceivedAt);
        jdbc.update("""
                INSERT INTO dev_device_command_event (
                    id, tenant_id, organization_id, asset_id, command_id,
                    edge_event_id, observation_stage, error_code
                ) VALUES (70, 20, 30, 40, 50, 60, 'ACCEPTED', NULL)
                """);
        jdbc.update("""
                INSERT INTO dev_device_command_event (
                    id, tenant_id, organization_id, asset_id, command_id,
                    edge_event_id, observation_stage, error_code
                ) VALUES (71, 20, 30, 40, 50, 61, 'MCU_ACCEPTED', NULL)
                """);

        DeliveryCommandObservationReconciliationItemService items = mock(
                DeliveryCommandObservationReconciliationItemService.class);
        new DeliveryCommandObservationReconciliationScheduler(jdbc, items)
                .reconcileHistoricalDeliveryObservations();

        verifyNoInteractions(items);
    }

    @Test
    void candidatesComeFromStoredEvidenceAndDoNotDependOnTaskState() {
        String sql = DeliveryCommandObservationReconciliationScheduler
                .FIND_CANDIDATE_COMMAND_IDS_SQL
                .toUpperCase(Locale.ROOT);

        assertThat(sql)
                .contains(
                        "DEV_DEVICE_COMMAND_EVENT",
                        "DEV_EDGE_EVENT",
                        "DEV_DELIVERY_SESSION",
                        "AUTHORIZATION_QUEUED",
                        "MCU_ACCEPTED",
                        "PRE_START_FAILED",
                        "EDGE_EVENT_SEQUENCE")
                .doesNotContain("OPS_RELIABLE_TASK");
    }

    @Test
    void oneBrokenHistoricalCommandDoesNotBlockTheRestOfTheBatch() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        DeliveryCommandObservationReconciliationItemService items = mock(
                DeliveryCommandObservationReconciliationItemService.class);
        when(jdbc.query(
                eq(DeliveryCommandObservationReconciliationScheduler
                        .FIND_CANDIDATE_COMMAND_IDS_SQL),
                any(org.springframework.jdbc.core.RowMapper.class),
                eq(100)))
                .thenReturn(List.of(11L, 12L));
        doThrow(new IllegalStateException("broken legacy row"))
                .when(items).reconcile(11L);

        new DeliveryCommandObservationReconciliationScheduler(jdbc, items)
                .reconcileHistoricalDeliveryObservations();

        verify(items).reconcile(11L);
        verify(items).reconcile(12L);
    }
}
