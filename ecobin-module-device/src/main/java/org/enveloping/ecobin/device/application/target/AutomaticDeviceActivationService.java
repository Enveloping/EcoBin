package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationPortSnapshot;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationReleaseRequest;
import org.enveloping.ecobin.framework.reliability.DeviceCommandTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskRegistrationPort;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskStatusPort;
import org.enveloping.ecobin.framework.reliability.ReliableTaskWake;
import org.enveloping.ecobin.framework.reliability.ReliableTaskWakePort;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.Statement;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;

/**
 * 收敛机构永久分配后的无人值守启用流程。
 *
 * <p>机构分配事务负责创建第一版完整配置；设备可靠回报配置已经应用后，本服务为厂家
 * 登记的每个真实初始袋创建独立皮重测量命令。这里没有“部署确认”或“经营开关”：配置
 * 与真实皮重事实齐全后，既有业务准入查询会直接放行健康投口。</p>
 */
@Service
public class AutomaticDeviceActivationService {

    private static final String CONFIGURATION_TASK_TYPE =
            "ENSURE_DEVICE_CONFIGURATION";
    private static final String CONFIGURATION_TARGET_TYPE =
            "CONFIGURATION_APPLICATION";
    private static final String BASELINE_TASK_TYPE =
            "MEASURE_EMPTY_BAG_BASELINE";
    private static final String BASELINE_TARGET_TYPE =
            "BASELINE_MEASUREMENT";
    private static final long COMMAND_VALIDITY_SECONDS =
            10L * 365 * 24 * 60 * 60;

    static final String LOCK_INITIAL_BASELINE_CAPACITY_SQL = """
            SELECT port_id
            FROM rec_port_capacity_state
            WHERE tenant_id = ?
              AND organization_id = ?
              AND asset_id = ?
            ORDER BY port_id
            FOR UPDATE
            """;

    static final String LOAD_INITIAL_BASELINE_FACTS_SQL = """
            SELECT port.id AS port_id, port.port_no,
                   factory_installation.id AS factory_installation_id,
                   factory_bag.id AS factory_bag_id,
                   current_bag.id AS current_bag_id,
                   current_bag.bag_uid AS current_bag_uid,
                   capacity.lock_version AS capacity_version,
                   capacity.current_bag_id AS capacity_current_bag_id,
                   capacity.baseline_state,
                   capacity.current_baseline_id,
                   current_baseline.bag_id AS current_baseline_bag_id,
                   snapshot.id AS snapshot_id,
                   snapshot.fullness_mode,
                   snapshot.configured_full_weight_g,
                   snapshot.fullness_settle_wait_ms,
                   snapshot.fullness_confirmation_wait_ms,
                   snapshot.weight_measurement_timeout_ms,
                   EXISTS (
                       SELECT 1
                       FROM rec_port_baseline_measurement active
                       WHERE active.port_id = port.id
                         AND active.status = 'PENDING'
                   ) AS active_measurement,
                   (
                       SELECT COUNT(*)
                       FROM rec_port_baseline_measurement previous
                       WHERE previous.port_id = port.id
                         AND previous.status = 'FAILED'
                   ) AS failed_measurements,
                   (
                       SELECT MAX(previous.completed_at)
                       FROM rec_port_baseline_measurement previous
                       WHERE previous.port_id = port.id
                         AND previous.status = 'FAILED'
                   ) AS last_failed_at
            FROM dev_port port
            JOIN dev_factory_installed_bag factory_installation
              ON factory_installation.asset_id = port.asset_id
             AND factory_installation.port_no = port.port_no
            JOIN rec_bag factory_bag
              ON factory_bag.tenant_id = port.tenant_id
             AND factory_bag.organization_id = port.organization_id
             AND factory_bag.bag_code = factory_installation.bag_code
            JOIN rec_bag_current_occupancy occupancy
              ON occupancy.tenant_id = port.tenant_id
             AND occupancy.organization_id = port.organization_id
             AND occupancy.port_id = port.id
             AND occupancy.occupancy_type = 'PORT_BOUND'
            JOIN rec_bag current_bag
              ON current_bag.tenant_id = occupancy.tenant_id
             AND current_bag.organization_id = occupancy.organization_id
             AND current_bag.id = occupancy.bag_id
            JOIN rec_port_capacity_state capacity
              ON capacity.tenant_id = port.tenant_id
             AND capacity.organization_id = port.organization_id
             AND capacity.asset_id = port.asset_id
             AND capacity.port_id = port.id
            LEFT JOIN rec_port_weight_baseline current_baseline
              ON current_baseline.tenant_id = capacity.tenant_id
             AND current_baseline.organization_id = capacity.organization_id
             AND current_baseline.port_id = capacity.port_id
             AND current_baseline.id = capacity.current_baseline_id
            JOIN dev_port_config_snapshot snapshot
              ON snapshot.tenant_id = port.tenant_id
             AND snapshot.organization_id = port.organization_id
             AND snapshot.asset_id = port.asset_id
             AND snapshot.config_version_id = ?
             AND snapshot.port_id = port.id
            WHERE port.tenant_id = ?
              AND port.organization_id = ?
              AND port.asset_id = ?
            ORDER BY port.port_no
            """;

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final DeviceConfigurationCanonicalizer canonicalizer;
    private final RuntimeSnapshotPolicyProvider runtimeSnapshotPolicyProvider;
    private final InitialDeviceConfigurationFactory initialConfigurationFactory;
    private final ReliableDeviceTaskRegistrationPort taskRegistration;
    private final DeviceCommandTaskRefFactory taskRefFactory;
    private final ReliableDeviceTaskStatusPort taskStatus;
    private final ReliableTaskWakePort taskWake;

    public AutomaticDeviceActivationService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            DeviceConfigurationCanonicalizer canonicalizer,
            RuntimeSnapshotPolicyProvider runtimeSnapshotPolicyProvider,
            InitialDeviceConfigurationFactory initialConfigurationFactory,
            ReliableDeviceTaskRegistrationPort taskRegistration,
            DeviceCommandTaskRefFactory taskRefFactory,
            ReliableDeviceTaskStatusPort taskStatus,
            ReliableTaskWakePort taskWake) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.canonicalizer = canonicalizer;
        this.runtimeSnapshotPolicyProvider = runtimeSnapshotPolicyProvider;
        this.initialConfigurationFactory = initialConfigurationFactory;
        this.taskRegistration = taskRegistration;
        this.taskRefFactory = taskRefFactory;
        this.taskStatus = taskStatus;
        this.taskWake = taskWake;
    }

    /** 在分配或可信设备事件所属的现有事务中收敛自动启用。 */
    @Transactional(propagation = Propagation.MANDATORY)
    public void reconcileInCurrentTransaction(
            long assetId,
            UUID correlationUid) {
        reconcile(assetId, correlationUid);
    }

    /** 为定时补偿提供独立事务，修复进程中断或旧版本留下的未完成资产。 */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void reconcileAsset(long assetId) {
        reconcile(assetId, UUID.randomUUID());
    }

    private void reconcile(long assetId, UUID correlationUid) {
        AssetFacts asset = lockAssignedAsset(assetId);
        if (asset == null) {
            return;
        }
        ConfigurationFacts configuration = latestConfiguration(asset);
        if (configuration == null) {
            configuration = createInitialConfiguration(asset, correlationUid);
        }
        if ("FAILED".equals(configuration.applicationStatus())) {
            retryFailedConfiguration(configuration);
            return;
        }
        if ("APPLIED".equals(configuration.applicationStatus())) {
            ensureInitialBagBaselines(
                    asset,
                    configuration,
                    correlationUid);
        }
    }

    private AssetFacts lockAssignedAsset(long assetId) {
        List<AssetFacts> rows = jdbc.query("""
                        SELECT id, tenant_id, organization_id,
                               hardware_sn, model_name,
                               expected_port_count, lifecycle_status,
                               acceptance_status
                        FROM dev_device_asset
                        WHERE id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new AssetFacts(
                        rs.getLong("id"),
                        nullableLong(rs, "tenant_id"),
                        nullableLong(rs, "organization_id"),
                        rs.getString("hardware_sn"),
                        rs.getString("model_name"),
                        rs.getInt("expected_port_count"),
                        rs.getString("lifecycle_status"),
                        rs.getString("acceptance_status")),
                assetId);
        if (rows.size() != 1) {
            return null;
        }
        AssetFacts asset = rows.getFirst();
        if (asset.tenantId() == null
                || asset.organizationId() == null
                || !"NORMAL".equals(asset.lifecycleStatus())
                || !"PASSED".equals(asset.acceptanceStatus())) {
            return null;
        }
        return asset;
    }

    private ConfigurationFacts latestConfiguration(AssetFacts asset) {
        List<ConfigurationFacts> rows = jdbc.query("""
                        SELECT version.id AS config_id,
                               version.version_no,
                               LOWER(HEX(version.content_sha256))
                                   AS content_sha256,
                               LOWER(HEX(version.mcu_payload_sha256))
                                   AS mcu_payload_sha256,
                               application.id AS application_id,
                               application.application_uid,
                               application.status AS application_status,
                               application.last_failure_code,
                               application.last_failure_at
                        FROM dev_config_version version
                        JOIN dev_config_application application
                          ON application.tenant_id = version.tenant_id
                         AND application.organization_id =
                             version.organization_id
                         AND application.asset_id = version.asset_id
                         AND application.config_version_id = version.id
                        WHERE version.tenant_id = ?
                          AND version.organization_id = ?
                          AND version.asset_id = ?
                        ORDER BY version.version_no DESC
                        LIMIT 1
                        """,
                (rs, ignored) -> new ConfigurationFacts(
                        rs.getLong("config_id"),
                        rs.getLong("version_no"),
                        rs.getString("content_sha256"),
                        rs.getString("mcu_payload_sha256"),
                        rs.getLong("application_id"),
                        UUID.fromString(rs.getString("application_uid")),
                        rs.getString("application_status"),
                        rs.getString("last_failure_code"),
                        nullableLocalDateTime(rs, "last_failure_at")),
                asset.tenantId(),
                asset.organizationId(),
                asset.id());
        return rows.isEmpty() ? null : rows.getFirst();
    }

    private ConfigurationFacts createInitialConfiguration(
            AssetFacts asset,
            UUID correlationUid) {
        ConfigurationReleaseRequest request =
                initialConfigurationFactory.create(
                        asset.hardwareSn(),
                        asset.modelCode(),
                        asset.portCount());
        RuntimeSnapshotPolicyProvider.Policy runtimePolicy =
                runtimeSnapshotPolicyProvider.current();
        DeviceConfigurationCanonicalizer.NormalizedConfiguration normalized =
                canonicalizer.normalize(
                        request,
                        asset.portCount(),
                        runtimePolicy.fallbackIntervalMs(),
                        RuntimeSnapshotPolicyProvider.FIXED_MISS_THRESHOLD);
        long versionNo = 1;
        byte[] mcuPayloadSha256 = canonicalizer.mcuPayloadSha256(
                versionNo, normalized);
        LocalDateTime now = databaseNow();
        long configurationId = insertAndReturnKey("""
                INSERT INTO dev_config_version (
                    tenant_id, organization_id, asset_id,
                    version_no, schema_version, device_display_name,
                    location_address, latitude, longitude,
                    edge_heartbeat_interval_ms,
                    edge_heartbeat_miss_threshold,
                    runtime_snapshot_policy_version_no,
                    mcu_heartbeat_interval_ms,
                    mcu_heartbeat_miss_threshold,
                    door_close_retry_limit,
                    continue_delivery_wait_ms,
                    negative_weight_threshold_g,
                    delivery_auto_close_ms,
                    weight_measurement_timeout_ms,
                    delivery_door_travel_wait_ms,
                    clean_solenoid_pulse_ms,
                    smoke_monitoring_enabled,
                    content_sha256, mcu_payload_sha256,
                    publication_source,
                    published_by_staff_account_id,
                    published_at, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, 'SYSTEM', NULL, ?, ?
                )
                """,
                asset.tenantId(),
                asset.organizationId(),
                asset.id(),
                versionNo,
                DeviceConfigurationCanonicalizer.CONFIGURATION_SCHEMA_VERSION,
                normalized.device().displayName(),
                normalized.device().address(),
                nullableDecimal(normalized.device().latitude()),
                nullableDecimal(normalized.device().longitude()),
                normalized.device().edgeHeartbeatIntervalMs(),
                normalized.device().edgeHeartbeatMissThreshold(),
                runtimePolicy.version(),
                normalized.device().mcuHeartbeatIntervalMs(),
                normalized.device().mcuHeartbeatMissThreshold(),
                normalized.device().doorCloseRetryLimit(),
                normalized.device().continueDeliveryWaitMs(),
                normalized.device().negativeWeightThresholdGram(),
                normalized.device().deliveryAutoCloseMs(),
                normalized.device().weightMeasurementTimeoutMs(),
                normalized.device().deliveryDoorTravelWaitMs(),
                normalized.device().cleanSolenoidPulseMs(),
                normalized.device().smokeMonitoringEnabled(),
                normalized.contentSha256(),
                mcuPayloadSha256,
                now,
                now);

        Map<Integer, Long> portIds = jdbc.query("""
                        SELECT id, port_no
                        FROM dev_port
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                        ORDER BY port_no
                        """,
                rs -> {
                    Map<Integer, Long> result = new LinkedHashMap<>();
                    while (rs.next()) {
                        result.put(rs.getInt("port_no"), rs.getLong("id"));
                    }
                    return result;
                },
                asset.tenantId(),
                asset.organizationId(),
                asset.id());
        if (portIds.size() != asset.portCount()) {
            throw new IllegalStateException(
                    "automatic configuration port set is incomplete");
        }
        for (DeviceConfigurationCanonicalizer.NormalizedPort port
                : normalized.ports()) {
            ConfigurationPortSnapshot view = port.view();
            Long portId = portIds.get(view.portNo());
            if (portId == null) {
                throw new IllegalStateException(
                        "automatic configuration port is missing");
            }
            requireSingle(jdbc.update("""
                            INSERT INTO dev_port_config_snapshot (
                                tenant_id, organization_id, asset_id,
                                config_version_id, port_id, display_name,
                                business_enabled, unit_price_yuan_per_kg,
                                fullness_mode, configured_full_weight_g,
                                delivery_settle_delay_ms,
                                fullness_settle_wait_ms,
                                fullness_sensor_kind,
                                fullness_distance_threshold_mm,
                                fullness_sample_count,
                                fullness_min_valid_sample_count,
                                fullness_echo_timeout_us,
                                fullness_confirmation_wait_ms,
                                door_auto_close_timeout_ms,
                                weight_stable_window_ms,
                                weight_maximum_fluctuation_g,
                                weight_required_sample_count,
                                weight_measurement_timeout_ms,
                                weight_minimum_g, weight_maximum_g,
                                calibration_version,
                                infrared_sample_timeout_ms,
                                delivery_door_operation_timeout_ms,
                                created_at
                            ) VALUES (
                                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                ?, ?, ?, ?, ?, ?
                            )
                            """,
                    asset.tenantId(),
                    asset.organizationId(),
                    asset.id(),
                    configurationId,
                    portId,
                    view.displayName(),
                    view.enabled(),
                    new BigDecimal(view.unitPriceYuanPerKg()),
                    view.fullnessMode(),
                    port.configuredFullWeightGrams(),
                    view.deliverySettleDelayMs(),
                    view.fullnessInitialDelayMs(),
                    view.fullnessSensorKind(),
                    view.fullnessDistanceThresholdMm(),
                    view.fullnessSampleCount(),
                    view.fullnessMinimumValidSampleCount(),
                    view.fullnessEchoTimeoutUs(),
                    view.fullnessRecheckDelayMs(),
                    view.doorAutoCloseTimeoutMs(),
                    view.weightStableWindowMs(),
                    view.weightMaximumFluctuationGram(),
                    view.weightRequiredSampleCount(),
                    view.weightMeasurementTimeoutMs(),
                    view.weightMinimumGram(),
                    view.weightMaximumGram(),
                    view.calibrationVersion(),
                    view.infraredSampleTimeoutMs(),
                    view.deliveryDoorOperationTimeoutMs(),
                    now));
        }

        UUID applicationUid = UUID.randomUUID();
        long applicationId = insertAndReturnKey("""
                INSERT INTO dev_config_application (
                    application_uid, tenant_id, organization_id,
                    asset_id, config_version_id, status,
                    reported_version_no, reported_content_sha256,
                    reported_mcu_payload_sha256, edge_persisted_at,
                    mcu_synced_at, applied_at, last_failure_at,
                    last_failure_code, lock_version, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, 'PENDING',
                    NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                    0, ?, ?
                )
                """,
                applicationUid.toString(),
                asset.tenantId(),
                asset.organizationId(),
                asset.id(),
                configurationId,
                now,
                now);

        Map<String, Object> payload = canonicalizer.commandPayload(
                applicationUid.toString(),
                versionNo,
                normalized,
                mcuPayloadSha256);
        byte[] payloadSha256 = canonicalizer.payloadSha256(payload);
        UUID commandUid = UUID.randomUUID();
        Instant issuedAt = now.toInstant(ZoneOffset.UTC);
        Map<String, Object> envelope = commandEnvelope(
                commandUid,
                "APPLY_CONFIGURATION",
                asset.hardwareSn(),
                CONFIGURATION_TARGET_TYPE,
                applicationUid,
                issuedAt,
                payload,
                payloadSha256);
        byte[] envelopeSha256 = canonicalizer.payloadSha256(envelope);
        long commandId = insertCommand(
                asset,
                commandUid,
                "APPLY_CONFIGURATION",
                applicationId,
                null,
                envelope,
                envelopeSha256,
                now);

        Map<String, Object> taskSnapshot = taskSnapshot(
                commandUid,
                "APPLY_CONFIGURATION",
                asset.hardwareSn(),
                CONFIGURATION_TARGET_TYPE,
                applicationUid,
                envelopeSha256);
        taskRegistration.register(new ReliableDeviceTaskRegistration(
                CONFIGURATION_TASK_TYPE,
                CONFIGURATION_TASK_TYPE + ":"
                        + applicationUid.toString().toUpperCase(Locale.ROOT),
                CONFIGURATION_TARGET_TYPE,
                applicationUid.toString(),
                taskRefFactory.issue(
                        asset.tenantId(),
                        asset.organizationId(),
                        asset.id(),
                        commandId),
                2,
                writeJson(taskSnapshot),
                envelopeSha256,
                correlationUid,
                null,
                12,
                true,
                now));
        return new ConfigurationFacts(
                configurationId,
                versionNo,
                normalized.contentSha256Hex(),
                canonicalizer.hex(mcuPayloadSha256),
                applicationId,
                applicationUid,
                "PENDING",
                null,
                null);
    }

    private void retryFailedConfiguration(
            ConfigurationFacts configuration) {
        if (configuration.lastFailureCode() != null
                && configuration.lastFailureCode()
                .startsWith("CONFIGURATION_REJECTED")) {
            return;
        }
        LocalDateTime now = databaseNow();
        if (configuration.lastFailureAt() != null
                && now.isBefore(configuration.lastFailureAt()
                .plusMinutes(1))) {
            return;
        }
        var task = taskStatus.find(
                        CONFIGURATION_TASK_TYPE,
                        CONFIGURATION_TARGET_TYPE,
                        configuration.applicationUid().toString(),
                        true)
                .orElseThrow(() -> new IllegalStateException(
                        "automatic configuration task is missing"));
        requireSingle(jdbc.update("""
                        UPDATE dev_config_application
                        SET status = 'PENDING',
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ? AND status = 'FAILED'
                        """,
                now,
                configuration.applicationId()));
        taskWake.wake(new ReliableTaskWake(
                task.taskUid(), "AUTOMATIC_CONFIGURATION_RETRY"));
    }

    private void ensureInitialBagBaselines(
            AssetFacts asset,
            ConfigurationFacts configuration,
            UUID correlationUid) {
        List<Long> lockedCapacityPortIds = jdbc.query(
                LOCK_INITIAL_BASELINE_CAPACITY_SQL,
                (rs, ignored) -> rs.getLong("port_id"),
                asset.tenantId(),
                asset.organizationId(),
                asset.id());
        if (lockedCapacityPortIds.size() != asset.portCount()) {
            throw new IllegalStateException(
                    "automatic baseline capacity states are incomplete");
        }

        List<PortBaselineFacts> ports = jdbc.query(
                LOAD_INITIAL_BASELINE_FACTS_SQL,
                (rs, ignored) -> new PortBaselineFacts(
                        rs.getLong("port_id"),
                        rs.getInt("port_no"),
                        rs.getLong("factory_installation_id"),
                        rs.getLong("factory_bag_id"),
                        rs.getLong("current_bag_id"),
                        UUID.fromString(rs.getString("current_bag_uid")),
                        rs.getLong("capacity_version"),
                        nullableLong(rs, "capacity_current_bag_id"),
                        rs.getString("baseline_state"),
                        nullableLong(rs, "current_baseline_id"),
                        nullableLong(rs, "current_baseline_bag_id"),
                        rs.getLong("snapshot_id"),
                        rs.getString("fullness_mode"),
                        rs.getLong("configured_full_weight_g"),
                        rs.getLong("fullness_settle_wait_ms"),
                        rs.getLong("fullness_confirmation_wait_ms"),
                        rs.getLong("weight_measurement_timeout_ms"),
                        rs.getBoolean("active_measurement"),
                        rs.getInt("failed_measurements"),
                        nullableLocalDateTime(rs, "last_failed_at")),
                configuration.configurationId(),
                asset.tenantId(),
                asset.organizationId(),
                asset.id());
        if (ports.size() != asset.portCount()) {
            throw new IllegalStateException(
                    "automatic baseline port facts are incomplete");
        }
        for (PortBaselineFacts port : ports) {
            if (!requiresAutomaticInitialBaseline(
                    port.factoryBagId(),
                    port.currentBagId(),
                    port.capacityCurrentBagId(),
                    port.baselineState(),
                    port.currentBaselineId(),
                    port.currentBaselineBagId())) {
                continue;
            }
            if (port.activeMeasurement()) {
                continue;
            }
            if (!baselineRetryDue(port, databaseNow())) {
                continue;
            }
            createBaselineMeasurement(
                    asset,
                    configuration,
                    port,
                    correlationUid);
        }
    }

    private void createBaselineMeasurement(
            AssetFacts asset,
            ConfigurationFacts configuration,
            PortBaselineFacts port,
            UUID correlationUid) {
        LocalDateTime now = databaseNow();
        UUID measurementUid = UUID.randomUUID();
        byte[] ruleFingerprint = fullnessRuleFingerprint(port);
        long measurementId = insertAndReturnKey("""
                INSERT INTO rec_port_baseline_measurement (
                    measurement_uid, tenant_id, organization_id,
                    asset_id, port_id, bag_id,
                    device_config_version_id, port_config_snapshot_id,
                    capacity_lock_version_snapshot,
                    fullness_rule_fingerprint,
                    initiator_kind, platform_admin_id, staff_account_id,
                    status, physical_result_id, stable_total_weight_g,
                    fault_code, result_baseline_id,
                    started_at, completed_at, lock_version,
                    created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    'SYSTEM', NULL, NULL,
                    'PENDING', NULL, NULL, NULL, NULL,
                    ?, NULL, 0, ?, ?
                )
                """,
                measurementUid.toString(),
                asset.tenantId(),
                asset.organizationId(),
                asset.id(),
                port.portId(),
                port.currentBagId(),
                configuration.configurationId(),
                port.snapshotId(),
                port.capacityVersion(),
                ruleFingerprint,
                now,
                now,
                now);

        Map<String, Object> config = new LinkedHashMap<>();
        config.put("version", configuration.versionNo());
        config.put("contentSha256", configuration.contentSha256());
        config.put("mcuPayloadSha256", configuration.mcuPayloadSha256());
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("measurementUid", measurementUid.toString());
        payload.put("portNo", port.portNo());
        payload.put("bagUid", port.currentBagUid().toString());
        payload.put("emptyBagConfirmed", true);
        payload.put("measurementTimeoutMs", port.measurementTimeoutMs());
        payload.put("config", config);
        byte[] payloadSha256 = canonicalizer.payloadSha256(payload);
        UUID commandUid = UUID.randomUUID();
        Instant issuedAt = now.toInstant(ZoneOffset.UTC);
        Map<String, Object> envelope = commandEnvelope(
                commandUid,
                BASELINE_TASK_TYPE,
                asset.hardwareSn(),
                BASELINE_TARGET_TYPE,
                measurementUid,
                issuedAt,
                payload,
                payloadSha256);
        byte[] envelopeSha256 = canonicalizer.payloadSha256(envelope);
        long commandId = insertCommand(
                asset,
                commandUid,
                BASELINE_TASK_TYPE,
                null,
                measurementId,
                envelope,
                envelopeSha256,
                now);
        requireSingle(jdbc.update("""
                        UPDATE dev_factory_installed_bag
                        SET tare_status = 'MEASURING',
                            last_failure_code = NULL,
                            updated_at = ?
                        WHERE id = ?
                        """,
                now,
                port.factoryInstallationId()));

        Map<String, Object> taskSnapshot = taskSnapshot(
                commandUid,
                BASELINE_TASK_TYPE,
                asset.hardwareSn(),
                BASELINE_TARGET_TYPE,
                measurementUid,
                envelopeSha256);
        taskRegistration.register(new ReliableDeviceTaskRegistration(
                BASELINE_TASK_TYPE,
                BASELINE_TASK_TYPE + ":"
                        + measurementUid.toString().toUpperCase(Locale.ROOT),
                BASELINE_TARGET_TYPE,
                measurementUid.toString(),
                taskRefFactory.issue(
                        asset.tenantId(),
                        asset.organizationId(),
                        asset.id(),
                        commandId),
                2,
                writeJson(taskSnapshot),
                envelopeSha256,
                correlationUid,
                configuration.applicationUid(),
                12,
                false,
                now));
    }

    private long insertCommand(
            AssetFacts asset,
            UUID commandUid,
            String commandType,
            Long configurationApplicationId,
            Long baselineMeasurementId,
            Map<String, Object> envelope,
            byte[] envelopeSha256,
            LocalDateTime now) {
        return insertAndReturnKey("""
                INSERT INTO dev_device_command (
                    command_uid, tenant_id, organization_id,
                    asset_id, command_type, delivery_session_id,
                    clean_operation_id, config_application_id,
                    fullness_detection_id, baseline_measurement_id,
                    payload_schema_version, semantic_payload,
                    semantic_payload_sha256, physical_state, queued_at,
                    edge_accepted_at, physical_started_at,
                    physical_ended_at, lock_version, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, NULL, NULL, ?, NULL, ?,
                    2, CAST(? AS JSON), ?, 'QUEUED', ?,
                    NULL, NULL, NULL, 0, ?, ?
                )
                """,
                commandUid.toString(),
                asset.tenantId(),
                asset.organizationId(),
                asset.id(),
                commandType,
                configurationApplicationId,
                baselineMeasurementId,
                writeJson(envelope),
                envelopeSha256,
                now,
                now,
                now);
    }

    private Map<String, Object> commandEnvelope(
            UUID commandUid,
            String commandType,
            String hardwareSn,
            String targetType,
            UUID targetUid,
            Instant issuedAt,
            Map<String, Object> payload,
            byte[] payloadSha256) {
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("schemaVersion", 2);
        envelope.put("commandUid", commandUid.toString());
        envelope.put("commandType", commandType);
        envelope.put("targetDeviceName", hardwareSn);
        envelope.put(
                "target",
                Map.of("type", targetType, "uid", targetUid.toString()));
        envelope.put("issuedAt", issuedAt.toString());
        envelope.put(
                "expiresAt",
                issuedAt.plusSeconds(COMMAND_VALIDITY_SECONDS).toString());
        envelope.put("payloadSchemaVersion", 2);
        envelope.put("payloadSha256", canonicalizer.hex(payloadSha256));
        envelope.put("payload", payload);
        envelope.put("cosGrant", null);
        return envelope;
    }

    private Map<String, Object> taskSnapshot(
            UUID commandUid,
            String commandType,
            String hardwareSn,
            String targetType,
            UUID targetUid,
            byte[] envelopeSha256) {
        Map<String, Object> snapshot = new LinkedHashMap<>();
        snapshot.put("schemaVersion", 2);
        snapshot.put("commandUid", commandUid.toString());
        snapshot.put("commandType", commandType);
        snapshot.put("targetDeviceName", hardwareSn);
        snapshot.put(
                "target",
                Map.of("type", targetType, "uid", targetUid.toString()));
        snapshot.put("payloadSchemaVersion", 2);
        snapshot.put(
                "semanticPayloadSha256",
                canonicalizer.hex(envelopeSha256));
        return snapshot;
    }

    private static byte[] fullnessRuleFingerprint(PortBaselineFacts port) {
        String value = String.join(
                "|",
                "FULLNESS_RULE_V1",
                port.fullnessMode(),
                Long.toString(port.configuredFullWeightGrams()),
                Long.toString(port.fullnessSettleWaitMs()),
                Long.toString(port.fullnessConfirmationWaitMs()),
                Long.toString(port.measurementTimeoutMs()));
        try {
            return MessageDigest.getInstance("SHA-256").digest(
                    value.getBytes(StandardCharsets.UTF_8));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
    }

    private long insertAndReturnKey(String sql, Object... args) {
        org.springframework.jdbc.support.GeneratedKeyHolder holder =
                new org.springframework.jdbc.support.GeneratedKeyHolder();
        jdbc.update(connection -> {
            var statement = connection.prepareStatement(
                    sql, Statement.RETURN_GENERATED_KEYS);
            for (int index = 0; index < args.length; index++) {
                statement.setObject(index + 1, args[index]);
            }
            return statement;
        }, holder);
        Number key = holder.getKey();
        if (key == null) {
            throw new IllegalStateException(
                    "automatic activation generated key is missing");
        }
        return key.longValue();
    }

    private LocalDateTime databaseNow() {
        LocalDateTime now = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        if (now == null) {
            throw new IllegalStateException("database time is unavailable");
        }
        return now;
    }

    private String writeJson(Object value) {
        return objectMapper.writeValueAsString(value);
    }

    private static BigDecimal nullableDecimal(String value) {
        return value == null ? null : new BigDecimal(value);
    }

    private static Long nullableLong(
            java.sql.ResultSet resultSet,
            String column) throws java.sql.SQLException {
        long value = resultSet.getLong(column);
        return resultSet.wasNull() ? null : value;
    }

    private static LocalDateTime nullableLocalDateTime(
            java.sql.ResultSet resultSet,
            String column) throws java.sql.SQLException {
        java.sql.Timestamp value = resultSet.getTimestamp(column);
        return value == null ? null : value.toLocalDateTime();
    }

    private static boolean baselineRetryDue(
            PortBaselineFacts port,
            LocalDateTime now) {
        if (port.failedMeasurements() == 0
                || port.lastFailedAt() == null) {
            return true;
        }
        int exponent = Math.min(port.failedMeasurements() - 1, 7);
        long delaySeconds = Math.min(3_600L, 30L << exponent);
        return !now.isBefore(port.lastFailedAt().plusSeconds(delaySeconds));
    }

    static boolean requiresAutomaticInitialBaseline(
            long factoryBagId,
            long currentBagId,
            Long capacityCurrentBagId,
            String baselineState,
            Long currentBaselineId,
            Long currentBaselineBagId) {
        if (!"VALID".equals(baselineState)) {
            return currentBagId == factoryBagId;
        }
        if (currentBaselineId == null
                || capacityCurrentBagId == null
                || currentBaselineBagId == null
                || capacityCurrentBagId != currentBagId
                || currentBaselineBagId != currentBagId) {
            throw new IllegalStateException(
                    "automatic current baseline does not match current bag");
        }
        return false;
    }

    private static void requireSingle(int affected) {
        if (affected != 1) {
            throw new IllegalStateException(
                    "automatic activation changed " + affected + " rows");
        }
    }

    private record AssetFacts(
            long id,
            Long tenantId,
            Long organizationId,
            String hardwareSn,
            String modelCode,
            int portCount,
            String lifecycleStatus,
            String acceptanceStatus) {
    }

    private record ConfigurationFacts(
            long configurationId,
            long versionNo,
            String contentSha256,
            String mcuPayloadSha256,
            long applicationId,
            UUID applicationUid,
            String applicationStatus,
            String lastFailureCode,
            LocalDateTime lastFailureAt) {
    }

    private record PortBaselineFacts(
            long portId,
            int portNo,
            long factoryInstallationId,
            long factoryBagId,
            long currentBagId,
            UUID currentBagUid,
            long capacityVersion,
            Long capacityCurrentBagId,
            String baselineState,
            Long currentBaselineId,
            Long currentBaselineBagId,
            long snapshotId,
            String fullnessMode,
            long configuredFullWeightGrams,
            long fullnessSettleWaitMs,
            long fullnessConfirmationWaitMs,
            long measurementTimeoutMs,
            boolean activeMeasurement,
            int failedMeasurements,
            LocalDateTime lastFailedAt) {
    }
}
