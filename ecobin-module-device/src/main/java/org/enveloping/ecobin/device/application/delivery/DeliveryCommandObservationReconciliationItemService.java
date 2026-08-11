package org.enveloping.ecobin.device.application.delivery;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;

/** Replays immutable delivery evidence into one still-active business session. */
@Service
public class DeliveryCommandObservationReconciliationItemService {

    static final String DATABASE_NOW_SQL = "SELECT CURRENT_TIMESTAMP(3)";

    static final String LOAD_OBSERVATIONS_SQL = """
            SELECT command_row.tenant_id,
                   command_row.organization_id,
                   command_row.asset_id,
                   command_row.delivery_session_id,
                   command_event.observed_command_type,
                   command_event.observation_stage,
                   command_event.error_code,
                   edge_event.device_occurred_at,
                   edge_event.backend_received_at
            FROM dev_device_command command_row
            JOIN dev_device_command_event command_event
              ON command_event.command_id = command_row.id
             AND command_event.tenant_id = command_row.tenant_id
             AND command_event.organization_id = command_row.organization_id
             AND command_event.asset_id = command_row.asset_id
            JOIN dev_edge_event edge_event
              ON edge_event.id = command_event.edge_event_id
             AND edge_event.tenant_id = command_event.tenant_id
             AND edge_event.organization_id = command_event.organization_id
             AND edge_event.asset_id = command_event.asset_id
            WHERE command_row.id = ?
              AND command_row.command_type = 'START_DELIVERY_SESSION'
              AND command_row.delivery_session_id IS NOT NULL
            ORDER BY edge_event.edge_event_sequence, command_event.id
            """;

    private final JdbcTemplate jdbc;
    private final ApplyDeliveryCommandObservationService projector;

    public DeliveryCommandObservationReconciliationItemService(
            JdbcTemplate jdbc,
            ApplyDeliveryCommandObservationService projector) {
        this.jdbc = jdbc;
        this.projector = projector;
    }

    @Transactional(
            propagation = Propagation.REQUIRES_NEW,
            isolation = Isolation.READ_COMMITTED)
    public void reconcile(long commandId) {
        List<HistoricalObservation> observations = jdbc.query(
                LOAD_OBSERVATIONS_SQL,
                (rs, ignored) -> new HistoricalObservation(
                        rs.getLong("tenant_id"),
                        rs.getLong("organization_id"),
                        rs.getLong("asset_id"),
                        rs.getLong("delivery_session_id"),
                        rs.getString("observed_command_type"),
                        rs.getString("observation_stage"),
                        rs.getString("error_code"),
                        rs.getObject("device_occurred_at", LocalDateTime.class),
                        rs.getObject("backend_received_at", LocalDateTime.class)),
                commandId);
        if (observations.isEmpty()) {
            return;
        }
        LocalDateTime projectedAt = jdbc.queryForObject(
                DATABASE_NOW_SQL, LocalDateTime.class);
        if (projectedAt == null) {
            throw new IllegalStateException("database clock is unavailable");
        }
        for (HistoricalObservation observation : observations) {
            projector.apply(
                    observation.tenantId(),
                    observation.organizationId(),
                    observation.assetId(),
                    observation.deliverySessionId(),
                    observation.commandType(),
                    observation.stage(),
                    observation.errorCode(),
                    observation.occurredAt(),
                    observation.receivedAt(),
                    projectedAt);
        }
    }

    record HistoricalObservation(
            long tenantId,
            long organizationId,
            long assetId,
            long deliverySessionId,
            String commandType,
            String stage,
            String errorCode,
            LocalDateTime occurredAt,
            LocalDateTime receivedAt) {
    }
}
