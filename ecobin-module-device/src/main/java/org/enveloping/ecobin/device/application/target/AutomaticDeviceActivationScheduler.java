package org.enveloping.ecobin.device.application.target;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * 自动启用的补偿扫描器。
 *
 * <p>正常主链在机构分配和配置应用事件的事务里立即推进；本扫描只补偿进程中断、暂时
 * 数据库故障或皮重测量失败，不要求机构点击重试。</p>
 */
@Component
@ConditionalOnProperty(
        prefix = "ecobin.device.activation",
        name = "scheduler-enabled",
        havingValue = "true",
        matchIfMissing = true)
public class AutomaticDeviceActivationScheduler {

    private static final Logger LOGGER = LoggerFactory.getLogger(
            AutomaticDeviceActivationScheduler.class);

    private final JdbcTemplate jdbc;
    private final AutomaticDeviceActivationService activationService;

    public AutomaticDeviceActivationScheduler(
            JdbcTemplate jdbc,
            AutomaticDeviceActivationService activationService) {
        this.jdbc = jdbc;
        this.activationService = activationService;
    }

    @Scheduled(
            fixedDelayString =
                    "${ecobin.device.activation-reconcile-ms:30000}")
    public void reconcileIncompleteAssets() {
        List<Long> assetIds = jdbc.query("""
                        SELECT asset.id
                        FROM dev_device_asset asset
                        WHERE asset.tenant_id IS NOT NULL
                          AND asset.organization_id IS NOT NULL
                          AND asset.lifecycle_status = 'NORMAL'
                          AND asset.acceptance_status = 'PASSED'
                          AND (
                              NOT EXISTS (
                                  SELECT 1
                                  FROM dev_config_version version
                                  WHERE version.asset_id = asset.id
                              )
                              OR EXISTS (
                                  SELECT 1
                                  FROM dev_factory_installed_bag bag
                                  WHERE bag.asset_id = asset.id
                                    AND bag.tare_status <> 'READY'
                              )
                          )
                        ORDER BY asset.id
                        LIMIT 200
                        """,
                (rs, ignored) -> rs.getLong("id"));
        for (Long assetId : assetIds) {
            try {
                activationService.reconcileAsset(assetId);
            } catch (RuntimeException exception) {
                LOGGER.warn(
                        "automatic device activation reconciliation failed assetId={} type={}",
                        assetId,
                        exception.getClass().getSimpleName());
            }
        }
    }
}
