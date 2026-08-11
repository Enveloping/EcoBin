package org.enveloping.ecobin.device.application.delivery;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;

/** Repairs delivery projections missed before the projector was introduced. */
@Component
@ConditionalOnProperty(
        prefix = "ecobin.device.delivery-observation-reconciliation",
        name = "scheduler-enabled",
        havingValue = "true",
        matchIfMissing = true)
public class DeliveryCommandObservationReconciliationScheduler {

    private static final Logger LOGGER = LoggerFactory.getLogger(
            DeliveryCommandObservationReconciliationScheduler.class);
    private static final int BATCH_SIZE = 100;

    static final String FIND_CANDIDATE_COMMAND_IDS_SQL = """
            SELECT command_row.id
            FROM dev_device_command command_row
            JOIN dev_delivery_session delivery_session
              ON delivery_session.id = command_row.delivery_session_id
             AND delivery_session.tenant_id = command_row.tenant_id
             AND delivery_session.organization_id = command_row.organization_id
             AND delivery_session.asset_id = command_row.asset_id
            WHERE command_row.command_type = 'START_DELIVERY_SESSION'
              AND delivery_session.status IN (
                  'AUTHORIZATION_QUEUED',
                  'IN_PROGRESS',
                  'RESULT_PENDING_RECOVERY'
              )
              AND EXISTS (
                  SELECT 1
                  FROM dev_device_command_event command_event
                  JOIN dev_edge_event edge_event
                    ON edge_event.id = command_event.edge_event_id
                   AND edge_event.tenant_id = command_event.tenant_id
                   AND edge_event.organization_id = command_event.organization_id
                   AND edge_event.asset_id = command_event.asset_id
                  WHERE command_event.command_id = command_row.id
                    AND (
                        (
                            command_event.observation_stage = 'ACCEPTED'
                            AND (
                                delivery_session.first_edge_accepted_at IS NULL
                                OR delivery_session.first_edge_accepted_at >
                                    CASE
                                        WHEN edge_event.device_occurred_at IS NOT NULL
                                         AND edge_event.device_occurred_at >= delivery_session.created_at
                                         AND edge_event.device_occurred_at <= edge_event.backend_received_at
                                        THEN edge_event.device_occurred_at
                                        ELSE edge_event.backend_received_at
                                    END
                            )
                        )
                        OR (
                            command_event.observation_stage = 'MCU_ACCEPTED'
                            AND (
                                delivery_session.status = 'AUTHORIZATION_QUEUED'
                                OR delivery_session.first_edge_accepted_at IS NULL
                                OR delivery_session.first_physical_progress_at IS NULL
                                OR delivery_session.first_edge_accepted_at >
                                    CASE
                                        WHEN edge_event.device_occurred_at IS NOT NULL
                                         AND edge_event.device_occurred_at >= delivery_session.created_at
                                         AND edge_event.device_occurred_at <= edge_event.backend_received_at
                                        THEN edge_event.device_occurred_at
                                        ELSE edge_event.backend_received_at
                                    END
                                OR delivery_session.first_physical_progress_at >
                                    CASE
                                        WHEN edge_event.device_occurred_at IS NOT NULL
                                         AND edge_event.device_occurred_at >= delivery_session.created_at
                                         AND edge_event.device_occurred_at <= edge_event.backend_received_at
                                        THEN edge_event.device_occurred_at
                                        ELSE edge_event.backend_received_at
                                    END
                            )
                        )
                        OR (
                            command_event.observation_stage IN (
                                'REJECTED', 'PRE_START_FAILED'
                            )
                            AND delivery_session.status = 'AUTHORIZATION_QUEUED'
                            AND delivery_session.first_physical_progress_at IS NULL
                        )
                        OR (
                            command_event.observation_stage = 'FAILED'
                            AND COALESCE(command_event.error_code, '') <>
                                'EDGE_RESTARTED'
                            AND delivery_session.status <>
                                'RESULT_PENDING_RECOVERY'
                        )
                    )
              )
            ORDER BY (
                SELECT MIN(edge_event.edge_event_sequence)
                FROM dev_device_command_event command_event
                JOIN dev_edge_event edge_event
                  ON edge_event.id = command_event.edge_event_id
                WHERE command_event.command_id = command_row.id
            ), command_row.id
            LIMIT ?
            """;

    private final JdbcTemplate jdbc;
    private final DeliveryCommandObservationReconciliationItemService items;

    public DeliveryCommandObservationReconciliationScheduler(
            JdbcTemplate jdbc,
            DeliveryCommandObservationReconciliationItemService items) {
        this.jdbc = jdbc;
        this.items = items;
    }

    @Scheduled(fixedDelayString =
            "${ecobin.device.delivery-observation-reconciliation.scan-ms:30000}")
    public void reconcileHistoricalDeliveryObservations() {
        List<Long> commandIds = jdbc.query(
                FIND_CANDIDATE_COMMAND_IDS_SQL,
                (rs, ignored) -> rs.getLong("id"),
                BATCH_SIZE);
        for (Long commandId : commandIds) {
            try {
                items.reconcile(commandId);
            } catch (RuntimeException exception) {
                LOGGER.warn(
                        "historical delivery observation reconciliation failed commandId={} type={} reason={}",
                        commandId,
                        exception.getClass().getSimpleName(),
                        exception.getMessage());
            }
        }
    }
}
