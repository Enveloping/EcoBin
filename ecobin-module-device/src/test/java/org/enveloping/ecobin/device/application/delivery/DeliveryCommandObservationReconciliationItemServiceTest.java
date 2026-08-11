package org.enveloping.ecobin.device.application.delivery;

import org.junit.jupiter.api.Test;
import org.mockito.InOrder;
import org.springframework.jdbc.core.JdbcTemplate;

import java.time.LocalDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class DeliveryCommandObservationReconciliationItemServiceTest {

    @Test
    void replaysStoredObservationsInEdgeSequenceWithoutTouchingReliableTask() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        ApplyDeliveryCommandObservationService projector = mock(
                ApplyDeliveryCommandObservationService.class);
        LocalDateTime createdAt = LocalDateTime.of(2026, 8, 11, 10, 0);
        LocalDateTime acceptedAt = createdAt.plusSeconds(2);
        LocalDateTime acceptedReceivedAt = createdAt.plusSeconds(3);
        LocalDateTime mcuAt = createdAt.plusSeconds(4);
        LocalDateTime mcuReceivedAt = createdAt.plusSeconds(5);
        LocalDateTime replayedAt = createdAt.plusMinutes(2);
        var accepted = new DeliveryCommandObservationReconciliationItemService
                .HistoricalObservation(
                7L, 8L, 9L, 10L,
                "START_DELIVERY_SESSION", "ACCEPTED", null,
                acceptedAt, acceptedReceivedAt);
        var mcuAccepted = new DeliveryCommandObservationReconciliationItemService
                .HistoricalObservation(
                7L, 8L, 9L, 10L,
                "START_DELIVERY_SESSION", "MCU_ACCEPTED", null,
                mcuAt, mcuReceivedAt);
        when(jdbc.query(
                eq(DeliveryCommandObservationReconciliationItemService
                        .LOAD_OBSERVATIONS_SQL),
                any(org.springframework.jdbc.core.RowMapper.class),
                eq(71L)))
                .thenReturn(List.of(accepted, mcuAccepted));
        when(jdbc.queryForObject(
                DeliveryCommandObservationReconciliationItemService
                        .DATABASE_NOW_SQL,
                LocalDateTime.class))
                .thenReturn(replayedAt);

        new DeliveryCommandObservationReconciliationItemService(jdbc, projector)
                .reconcile(71L);

        InOrder ordered = inOrder(projector);
        ordered.verify(projector).apply(
                7L, 8L, 9L, 10L,
                "START_DELIVERY_SESSION", "ACCEPTED", null,
                acceptedAt, acceptedReceivedAt, replayedAt);
        ordered.verify(projector).apply(
                7L, 8L, 9L, 10L,
                "START_DELIVERY_SESSION", "MCU_ACCEPTED", null,
                mcuAt, mcuReceivedAt, replayedAt);
        assertThat(DeliveryCommandObservationReconciliationItemService
                .LOAD_OBSERVATIONS_SQL)
                .contains("ORDER BY edge_event.edge_event_sequence");
    }

    @Test
    void noStoredObservationDoesNothing() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        ApplyDeliveryCommandObservationService projector = mock(
                ApplyDeliveryCommandObservationService.class);
        when(jdbc.query(
                eq(DeliveryCommandObservationReconciliationItemService
                        .LOAD_OBSERVATIONS_SQL),
                any(org.springframework.jdbc.core.RowMapper.class),
                eq(71L)))
                .thenReturn(List.of());

        new DeliveryCommandObservationReconciliationItemService(jdbc, projector)
                .reconcile(71L);

        verifyNoInteractions(projector);
    }
}
