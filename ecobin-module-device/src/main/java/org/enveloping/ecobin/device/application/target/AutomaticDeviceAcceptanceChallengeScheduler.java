package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.port.DeviceAcceptanceChallengeCoordinatorPort;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;

/** Retries failed or timed-out factory challenges without a human button. */
@Component
public class AutomaticDeviceAcceptanceChallengeScheduler {

    private final JdbcTemplate jdbc;
    private final DeviceAcceptanceChallengeCoordinatorPort coordinator;
    private final boolean realExternalMode;

    public AutomaticDeviceAcceptanceChallengeScheduler(
            JdbcTemplate jdbc,
            DeviceAcceptanceChallengeCoordinatorPort coordinator,
            @Value("${ecobin.external.mode:fake}") String externalMode) {
        this.jdbc = jdbc;
        this.coordinator = coordinator;
        this.realExternalMode = "real".equalsIgnoreCase(externalMode);
    }

    @Scheduled(
            initialDelayString = "${ecobin.device.acceptance.scan-initial-delay-ms:1000}",
            fixedDelayString = "${ecobin.device.acceptance.scan-delay-ms:30000}")
    public void retryOnlineAssets() {
        if (!realExternalMode) {
            return;
        }
        List<Long> candidates = jdbc.queryForList("""
                        SELECT asset.id
                        FROM dev_device_asset asset
                        JOIN dev_device_transport_state transport
                          ON transport.asset_id = asset.id
                        WHERE asset.acceptance_status <> 'PASSED'
                          AND asset.lifecycle_status = 'NORMAL'
                          AND transport.onenet_connection_status = 'ONLINE'
                          AND (
                              SELECT COUNT(*)
                              FROM dev_factory_installed_bag bag
                              WHERE bag.asset_id = asset.id
                          ) = asset.expected_port_count
                        ORDER BY asset.id
                        LIMIT 20
                        """,
                Long.class);
        candidates.forEach(coordinator::requestIfNeeded);
    }
}
