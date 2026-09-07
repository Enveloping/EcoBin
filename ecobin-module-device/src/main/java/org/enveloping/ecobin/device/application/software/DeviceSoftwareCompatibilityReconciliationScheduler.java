package org.enveloping.ecobin.device.application.software;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;

/** Repairs update admission holds left behind before terminal reconciliation. */
@Component
@ConditionalOnProperty(
        prefix = "ecobin.device.software-compatibility-reconciliation",
        name = "scheduler-enabled",
        havingValue = "true",
        matchIfMissing = true)
public class DeviceSoftwareCompatibilityReconciliationScheduler {

    private static final Logger LOGGER = LoggerFactory.getLogger(
            DeviceSoftwareCompatibilityReconciliationScheduler.class);
    private static final int BATCH_SIZE = 100;

    static final String FIND_STALE_UPDATE_ADMISSION_ASSET_IDS_SQL = """
            SELECT projection.asset_id
            FROM dev_device_compatibility_projection projection
            WHERE projection.primary_reason_code =
                  'BUSINESS_RUNTIME_UPDATE_ACTIVE'
              AND NOT EXISTS (
                  SELECT 1
                  FROM dev_edge_software_deployment deployment
                  WHERE deployment.asset_id = projection.asset_id
                    AND deployment.deployment_status NOT IN (
                        'PLANNED', 'SUCCEEDED', 'ROLLED_BACK', 'DEFERRED',
                        'REJECTED', 'FAILED_LOCKED', 'CANCELLED'
                    )
              )
            ORDER BY projection.asset_id
            LIMIT ?
            """;

    private final JdbcTemplate jdbc;
    private final DeviceSoftwareCompatibilityService compatibility;

    public DeviceSoftwareCompatibilityReconciliationScheduler(
            JdbcTemplate jdbc,
            DeviceSoftwareCompatibilityService compatibility) {
        this.jdbc = jdbc;
        this.compatibility = compatibility;
    }

    @Scheduled(fixedDelayString =
            "${ecobin.device.software-compatibility-reconciliation.scan-ms:30000}")
    public void reconcileStaleUpdateAdmissions() {
        List<Long> assetIds = jdbc.query(
                FIND_STALE_UPDATE_ADMISSION_ASSET_IDS_SQL,
                (rs, ignored) -> rs.getLong("asset_id"),
                BATCH_SIZE);
        for (Long assetId : assetIds) {
            try {
                compatibility.reassessLatestFact(assetId);
            } catch (RuntimeException exception) {
                LOGGER.warn(
                        "stale update admission reconciliation failed assetId={} type={} reason={}",
                        assetId,
                        exception.getClass().getSimpleName(),
                        exception.getMessage());
            }
        }
    }
}
