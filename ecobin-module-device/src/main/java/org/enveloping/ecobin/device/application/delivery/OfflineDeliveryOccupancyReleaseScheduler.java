package org.enveloping.ecobin.device.application.delivery;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;

/** Finds delivery use-slots whose trusted continuous offline window expired. */
@Component
@ConditionalOnProperty(
        prefix = "ecobin.device.offline-occupancy-release",
        name = "scheduler-enabled",
        havingValue = "true",
        matchIfMissing = true)
public class OfflineDeliveryOccupancyReleaseScheduler {

    private static final Logger LOGGER = LoggerFactory.getLogger(
            OfflineDeliveryOccupancyReleaseScheduler.class);
    private static final int BATCH_SIZE = 100;

    static final String FIND_CANDIDATES_SQL = """
            SELECT session.id AS session_id,
                   session.asset_id
            FROM dev_delivery_session session
            JOIN dev_device_transport_state transport
              ON transport.asset_id = session.asset_id
            JOIN dev_device_occupancy occupancy
              ON occupancy.asset_id = session.asset_id
             AND occupancy.tenant_id = session.tenant_id
             AND occupancy.organization_id = session.organization_id
             AND occupancy.occupancy_kind = 'DELIVERY'
             AND occupancy.delivery_session_id = session.id
            WHERE session.status IN (
                    'PREPARED',
                    'AUTHORIZATION_QUEUED',
                    'IN_PROGRESS',
                    'RESULT_PENDING_RECOVERY'
              )
              AND session.ended_at IS NULL
              AND session.offline_occupancy_released_at IS NULL
              AND transport.onenet_connection_status = 'OFFLINE'
              AND transport.offline_since_at <
                  TIMESTAMPADD(SECOND, -600, UTC_TIMESTAMP(3))
            ORDER BY transport.offline_since_at, session.id
            LIMIT ?
            """;

    private final JdbcTemplate jdbc;
    private final OfflineDeliveryOccupancyReleaseService items;

    public OfflineDeliveryOccupancyReleaseScheduler(
            JdbcTemplate jdbc,
            OfflineDeliveryOccupancyReleaseService items) {
        this.jdbc = jdbc;
        this.items = items;
    }

    @Scheduled(fixedDelayString =
            "${ecobin.device.offline-occupancy-release.scan-ms:10000}")
    public void releaseExpiredOfflineDeliveryOccupancies() {
        List<Candidate> candidates = jdbc.query(
                FIND_CANDIDATES_SQL,
                (rs, ignored) -> new Candidate(
                        rs.getLong("session_id"),
                        rs.getLong("asset_id")),
                BATCH_SIZE);
        for (Candidate candidate : candidates) {
            try {
                items.releaseIfEligible(
                        candidate.sessionId(), candidate.assetId());
            } catch (RuntimeException exception) {
                LOGGER.warn(
                        "offline delivery occupancy release failed sessionId={} assetId={} type={} reason={}",
                        candidate.sessionId(),
                        candidate.assetId(),
                        exception.getClass().getSimpleName(),
                        exception.getMessage());
            }
        }
    }

    record Candidate(long sessionId, long assetId) {
    }
}
