package org.enveloping.ecobin.recycling.application.clean;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;

/** Finds clean use-slots whose trusted continuous offline window expired. */
@Component
@ConditionalOnProperty(
        prefix = "ecobin.recycling.offline-occupancy-release",
        name = "scheduler-enabled",
        havingValue = "true",
        matchIfMissing = true)
public class OfflineCleanOccupancyReleaseScheduler {

    private static final Logger LOGGER = LoggerFactory.getLogger(
            OfflineCleanOccupancyReleaseScheduler.class);
    private static final int BATCH_SIZE = 100;

    static final String FIND_CANDIDATES_SQL = """
            SELECT operation.id AS operation_id,
                   operation.asset_id
            FROM rec_clean_operation operation
            JOIN dev_device_transport_state transport
              ON transport.asset_id = operation.asset_id
            JOIN dev_device_occupancy occupancy
              ON occupancy.asset_id = operation.asset_id
             AND occupancy.tenant_id = operation.tenant_id
             AND occupancy.organization_id = operation.organization_id
             AND occupancy.occupancy_kind = 'CLEAN'
             AND occupancy.clean_operation_id = operation.id
            WHERE operation.status IN (
                    'PREPARED',
                    'EDGE_SAVED',
                    'IN_PROGRESS',
                    'RECOVERY_REQUIRED'
              )
              AND operation.ended_at IS NULL
              AND operation.offline_occupancy_released_at IS NULL
              AND transport.onenet_connection_status = 'OFFLINE'
              AND transport.offline_since_at <
                  TIMESTAMPADD(SECOND, -600, UTC_TIMESTAMP(3))
            ORDER BY transport.offline_since_at, operation.id
            LIMIT ?
            """;

    private final JdbcTemplate jdbc;
    private final OfflineCleanOccupancyReleaseService items;

    public OfflineCleanOccupancyReleaseScheduler(
            JdbcTemplate jdbc,
            OfflineCleanOccupancyReleaseService items) {
        this.jdbc = jdbc;
        this.items = items;
    }

    @Scheduled(fixedDelayString =
            "${ecobin.recycling.offline-occupancy-release.scan-ms:10000}")
    public void releaseExpiredOfflineCleanOccupancies() {
        List<Candidate> candidates = jdbc.query(
                FIND_CANDIDATES_SQL,
                (rs, ignored) -> new Candidate(
                        rs.getLong("operation_id"),
                        rs.getLong("asset_id")),
                BATCH_SIZE);
        for (Candidate candidate : candidates) {
            try {
                items.releaseIfEligible(
                        candidate.operationId(), candidate.assetId());
            } catch (RuntimeException exception) {
                LOGGER.warn(
                        "offline clean occupancy release failed operationId={} assetId={} type={} reason={}",
                        candidate.operationId(),
                        candidate.assetId(),
                        exception.getClass().getSimpleName(),
                        exception.getMessage());
            }
        }
    }

    record Candidate(long operationId, long assetId) {
    }
}
