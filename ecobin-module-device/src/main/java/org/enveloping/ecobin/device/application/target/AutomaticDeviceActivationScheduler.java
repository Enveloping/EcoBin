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

    static final String FIND_INCOMPLETE_ASSET_IDS_SQL = """
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
                      FROM dev_port port
                      JOIN dev_factory_installed_bag factory_installation
                        ON factory_installation.asset_id = port.asset_id
                       AND factory_installation.port_no = port.port_no
                      JOIN rec_bag factory_bag
                        ON factory_bag.tenant_id = port.tenant_id
                       AND factory_bag.organization_id = port.organization_id
                       AND factory_bag.bag_code = factory_installation.bag_code
                      JOIN rec_bag_current_occupancy current_occupancy
                        ON current_occupancy.tenant_id = port.tenant_id
                       AND current_occupancy.organization_id = port.organization_id
                       AND current_occupancy.port_id = port.id
                       AND current_occupancy.bag_id = factory_bag.id
                       AND current_occupancy.occupancy_type = 'PORT_BOUND'
                      JOIN rec_port_capacity_state capacity
                        ON capacity.tenant_id = port.tenant_id
                       AND capacity.organization_id = port.organization_id
                       AND capacity.asset_id = port.asset_id
                       AND capacity.port_id = port.id
                       AND capacity.current_bag_id = factory_bag.id
                      JOIN dev_config_version version
                        ON version.asset_id = port.asset_id
                       AND version.tenant_id = port.tenant_id
                       AND version.organization_id = port.organization_id
                       AND version.version_no = (
                           SELECT MAX(latest.version_no)
                           FROM dev_config_version latest
                           WHERE latest.asset_id = port.asset_id
                             AND latest.tenant_id = port.tenant_id
                             AND latest.organization_id =
                                 port.organization_id
                       )
                      JOIN dev_config_application application
                        ON application.asset_id = port.asset_id
                       AND application.tenant_id = port.tenant_id
                       AND application.organization_id =
                           port.organization_id
                       AND application.config_version_id = version.id
                       AND application.status = 'APPLIED'
                      WHERE port.asset_id = asset.id
                        AND capacity.baseline_state <> 'VALID'
                        AND NOT EXISTS (
                            SELECT 1
                            FROM rec_port_baseline_measurement active
                            WHERE active.port_id = port.id
                              AND active.status = 'PENDING'
                        )
                        AND (
                            SELECT COUNT(*)
                            FROM rec_port_baseline_measurement attempted
                            WHERE attempted.port_id = port.id
                              AND attempted.bag_id = factory_bag.id
                              AND attempted.device_config_version_id =
                                  version.id
                              AND attempted.initiator_kind = 'SYSTEM'
                        ) < 4
                        AND COALESCE(
                            (
                                SELECT terminal.fault_code
                                FROM rec_port_baseline_measurement terminal
                                WHERE terminal.port_id = port.id
                                  AND terminal.bag_id = factory_bag.id
                                  AND terminal.device_config_version_id =
                                      version.id
                                  AND terminal.initiator_kind = 'SYSTEM'
                                  AND terminal.status IN (
                                      'FAILED',
                                      'STALE_IGNORED',
                                      'TECHNICAL_ABORTED'
                                  )
                                ORDER BY terminal.completed_at DESC,
                                         terminal.id DESC
                                LIMIT 1
                            ),
                            ''
                        ) NOT IN (
                            'DEVICE_IDENTITY_UNRESOLVED',
                            'PERMANENT_TECHNICAL_FAILURE'
                        )
                  )
                  OR NOT EXISTS (
                      SELECT 1
                      FROM dev_config_version version
                      JOIN dev_runtime_snapshot_policy policy
                        ON policy.singleton_id = 1
                       AND version.runtime_snapshot_policy_version_no
                           = policy.policy_version
                      WHERE version.asset_id = asset.id
                        AND version.version_no = (
                            SELECT MAX(latest.version_no)
                            FROM dev_config_version latest
                            WHERE latest.asset_id = asset.id
                        )
                  )
              )
            ORDER BY asset.id
            LIMIT 200
            """;

    private final JdbcTemplate jdbc;
    private final TargetDeviceApplication targetDeviceApplication;

    public AutomaticDeviceActivationScheduler(
            JdbcTemplate jdbc,
            TargetDeviceApplication targetDeviceApplication) {
        this.jdbc = jdbc;
        this.targetDeviceApplication = targetDeviceApplication;
    }

    @Scheduled(
            fixedDelayString =
                    "${ecobin.device.activation-reconcile-ms:30000}")
    public void reconcileIncompleteAssets() {
        List<Long> assetIds = jdbc.query(
                FIND_INCOMPLETE_ASSET_IDS_SQL,
                (rs, ignored) -> rs.getLong("id"));
        for (Long assetId : assetIds) {
            try {
                targetDeviceApplication.reconcileAutomaticActivation(assetId);
            } catch (RuntimeException exception) {
                LOGGER.warn(
                        "automatic device activation reconciliation failed assetId={} type={} reason={}",
                        assetId,
                        exception.getClass().getSimpleName(),
                        exception.getMessage());
            }
        }
    }
}
