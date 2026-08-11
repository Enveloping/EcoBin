package org.enveloping.ecobin.device.application.delivery;

import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;

import java.util.List;
import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class DeliveryCommandObservationReconciliationSchedulerTest {

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
