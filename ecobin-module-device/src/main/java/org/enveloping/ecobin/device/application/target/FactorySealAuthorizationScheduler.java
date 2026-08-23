package org.enveloping.ecobin.device.application.target;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * Compensation scan for PASSED rows migrated from V52 or interrupted before
 * the ordinary acceptance transaction could be retried.
 */
@Component
public class FactorySealAuthorizationScheduler {

    private final JdbcTemplate jdbc;
    private final FactorySealAuthorizationService authorizations;
    private final boolean realExternalMode;

    public FactorySealAuthorizationScheduler(
            JdbcTemplate jdbc,
            FactorySealAuthorizationService authorizations,
            @Value("${ecobin.external.mode:fake}") String externalMode) {
        this.jdbc = jdbc;
        this.authorizations = authorizations;
        this.realExternalMode = "real".equalsIgnoreCase(externalMode);
    }

    @Scheduled(
            initialDelayString =
                    "${ecobin.device.factory-seal.scan-initial-delay-ms:2000}",
            fixedDelayString =
                    "${ecobin.device.factory-seal.scan-delay-ms:30000}")
    public void reconcilePassedAssets() {
        if (!realExternalMode) {
            return;
        }
        List<Long> assetIds = jdbc.queryForList("""
                        SELECT asset.id
                        FROM dev_device_asset asset
                        WHERE asset.acceptance_status = 'PASSED'
                          AND asset.acceptance_generation > 0
                          AND NOT EXISTS (
                              SELECT 1
                              FROM dev_factory_seal_authorization authorization
                              WHERE authorization.asset_id = asset.id
                                AND authorization.acceptance_generation =
                                    asset.acceptance_generation
                          )
                        ORDER BY asset.id
                        LIMIT 20
                        """,
                Long.class);
        assetIds.forEach(authorizations::ensureForAcceptedAsset);
    }
}
