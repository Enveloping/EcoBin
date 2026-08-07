package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.web.v1.DeviceModels.AcceptanceEvidenceView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.AssignOrganizationRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.AssignTenantRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ComputedOneNetMapping;
import org.enveloping.ecobin.device.web.v1.DeviceModels.CreateDeviceAssetRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationAcceptedView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationApplicationSummary;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationApplicationView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationDeviceSnapshot;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationPortSnapshot;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationReleaseRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationResynchronizationRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationVersionSummary;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationVersionView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.CursorPage;
import org.enveloping.ecobin.device.web.v1.DeviceModels.DeviceAssetView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.DeviceControlRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.FactoryInstalledBagRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.PageData;
import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.audit.SuccessfulAudit;
import org.enveloping.ecobin.framework.reliability.DeviceCommandTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskRegistrationPort;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskStatus;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskStatusPort;
import org.enveloping.ecobin.framework.reliability.ReliableTaskWake;
import org.enveloping.ecobin.framework.reliability.ReliableTaskWakePort;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.framework.web.TargetWebAuditRequestContext;
import org.enveloping.ecobin.identity.api.port.DeviceScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.query.DeviceScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedDeviceScope;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.security.SecureRandom;
import java.math.BigDecimal;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.function.Supplier;

/**
 * 永久设备资产的唯一管理入口。
 *
 * <p>设备不再拥有部署实例。平台只写一次租户，租户只写一次机构；禁用和报废只改变
 * 新业务准入，不清除归属，也不取消已经由后端创建的作业。</p>
 */
@Service
public class TargetDeviceApplication {

    private static final int MAX_PAGE_SIZE = 200;
    private static final int DEFAULT_CURSOR_LIMIT = 20;
    private static final int MAX_CURSOR_LIMIT = 100;
    private static final long RECOMMENDED_POLL_AFTER_MS = 3_000;
    private static final long CONFIGURATION_COMMAND_VALIDITY_SECONDS =
            10L * 365 * 24 * 60 * 60;
    private static final String CONFIGURATION_TASK_TYPE =
            "ENSURE_DEVICE_CONFIGURATION";
    private static final String CONFIGURATION_TARGET_TYPE =
            "CONFIGURATION_APPLICATION";
    private static final SecureRandom PUBLIC_CODE_RANDOM = new SecureRandom();

    private final JdbcTemplate jdbc;
    private final DeviceScopeAuthorizationPort authorizationPort;
    private final AuditPort auditPort;
    private final ObjectMapper objectMapper;
    private final DeviceConfigurationCanonicalizer canonicalizer;
    private final ReliableDeviceTaskRegistrationPort taskRegistrationPort;
    private final ReliableDeviceTaskStatusPort taskStatusPort;
    private final ReliableTaskWakePort taskWakePort;
    private final DeviceCommandTaskRefFactory taskRefFactory;
    private final String oneNetProductId;
    private final AutomaticDeviceActivationService activationService;

    public TargetDeviceApplication(
            JdbcTemplate jdbc,
            DeviceScopeAuthorizationPort authorizationPort,
            AuditPort auditPort,
            ObjectMapper objectMapper,
            AutomaticDeviceActivationService activationService,
            DeviceConfigurationCanonicalizer canonicalizer,
            ReliableDeviceTaskRegistrationPort taskRegistrationPort,
            ReliableDeviceTaskStatusPort taskStatusPort,
            ReliableTaskWakePort taskWakePort,
            DeviceCommandTaskRefFactory taskRefFactory,
            @Value("${onenet.product-id:}") String oneNetProductId) {
        this.jdbc = jdbc;
        this.authorizationPort = authorizationPort;
        this.auditPort = auditPort;
        this.objectMapper = objectMapper;
        this.activationService = activationService;
        this.canonicalizer = canonicalizer;
        this.taskRegistrationPort = taskRegistrationPort;
        this.taskStatusPort = taskStatusPort;
        this.taskWakePort = taskWakePort;
        this.taskRefFactory = taskRefFactory;
        this.oneNetProductId = blankToNull(oneNetProductId);
    }

    @Transactional(readOnly = true)
    public PageData<DeviceAssetView> listPlatformAssets(
            int requestedPage,
            int requestedPageSize,
            String hardwareSn,
            String lifecycleStatus,
            String acceptanceStatus) {
        authorize(true, null, null, "device.read");
        return listAssets(
                null,
                null,
                false,
                requestedPage,
                requestedPageSize,
                hardwareSn,
                lifecycleStatus,
                acceptanceStatus);
    }

    @Transactional(readOnly = true)
    public PageData<DeviceAssetView> listTenantAssets(
            int requestedPage,
            int requestedPageSize,
            String hardwareSn) {
        Scope scope = authorize(false, null, null, "device.read");
        return listAssets(
                scope.tenantId(),
                null,
                true,
                requestedPage,
                requestedPageSize,
                hardwareSn,
                null,
                null);
    }

    @Transactional(readOnly = true)
    public PageData<DeviceAssetView> listOrganizationAssets(
            String organizationCode,
            int requestedPage,
            int requestedPageSize,
            String hardwareSn) {
        Scope scope = authorize(
                false, null, organizationCode, "device.read");
        return listAssets(
                scope.tenantId(),
                scope.organizationId(),
                true,
                requestedPage,
                requestedPageSize,
                hardwareSn,
                null,
                null);
    }

    @Transactional(readOnly = true)
    public DeviceAssetView platformAsset(String hardwareSn) {
        authorize(true, null, null, "device.read");
        return findAssetView(normalizeHardwareSn(hardwareSn), null, null, false)
                .orElseThrow(TargetDeviceApplication::notFound);
    }

    @Transactional(readOnly = true)
    public DeviceAssetView tenantAsset(String hardwareSn) {
        Scope scope = authorize(false, null, null, "device.read");
        return findAssetView(
                normalizeHardwareSn(hardwareSn),
                scope.tenantId(),
                null,
                true).orElseThrow(TargetDeviceApplication::notFound);
    }

    @Transactional(readOnly = true)
    public DeviceAssetView organizationAsset(
            String organizationCode,
            String deviceCode) {
        Scope scope = authorize(
                false, null, organizationCode, "device.read");
        return findAssetViewByCode(
                normalizeDeviceCode(deviceCode),
                scope.tenantId(),
                scope.organizationId(),
                true).orElseThrow(TargetDeviceApplication::notFound);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public DeviceAssetView createAsset(
            UUID operationUid,
            CreateDeviceAssetRequest request) {
        Scope scope = authorize(true, null, null, "device.manage");
        NormalizedAssetCreate normalized = normalizeCreate(request);
        return command(
                operationUid,
                scope,
                "device.asset.create",
                "DEVICE_ASSET",
                normalized.hardwareSn(),
                normalized,
                DeviceAssetView.class,
                () -> createAsset(normalized));
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public DeviceAssetView assignTenant(
            UUID operationUid,
            String hardwareSn,
            AssignTenantRequest request) {
        Scope scope = authorize(true, null, null, "device.manage");
        String normalizedHardwareSn = normalizeHardwareSn(hardwareSn);
        if (request == null || request.expectedVersion() == null) {
            throw invalid("租户分配请求不完整");
        }
        String tenantCode = required(request.tenantCode(), 32, "tenantCode");
        Map<String, Object> normalized = Map.of(
                "tenantCode", tenantCode,
                "expectedVersion", request.expectedVersion());
        return command(
                operationUid,
                scope,
                "device.asset.assign-tenant",
                "DEVICE_ASSET",
                normalizedHardwareSn,
                normalized,
                DeviceAssetView.class,
                () -> assignTenant(
                        normalizedHardwareSn,
                        tenantCode,
                        request.expectedVersion()));
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public DeviceAssetView assignOrganization(
            UUID operationUid,
            String hardwareSn,
            AssignOrganizationRequest request) {
        Scope scope = authorize(
                false, null, null, "device.assignment.manage");
        requireEnabledTenant(scope);
        String normalizedHardwareSn = normalizeHardwareSn(hardwareSn);
        if (request == null || request.expectedVersion() == null) {
            throw invalid("机构分配请求不完整");
        }
        String organizationCode = required(
                request.organizationCode(), 32, "organizationCode");
        Map<String, Object> normalized = Map.of(
                "organizationCode", organizationCode,
                "expectedVersion", request.expectedVersion());
        return command(
                operationUid,
                scope,
                "device.asset.assign-organization",
                "DEVICE_ASSET",
                normalizedHardwareSn,
                normalized,
                DeviceAssetView.class,
                () -> assignOrganization(
                        operationUid,
                        scope,
                        normalizedHardwareSn,
                        organizationCode,
                        request.expectedVersion()));
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public DeviceAssetView disable(
            UUID operationUid,
            String hardwareSn,
            DeviceControlRequest request) {
        return control(operationUid, hardwareSn, request, "DISABLED");
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public DeviceAssetView restore(
            UUID operationUid,
            String hardwareSn,
            DeviceControlRequest request) {
        return control(operationUid, hardwareSn, request, "NORMAL");
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public DeviceAssetView retire(
            UUID operationUid,
            String hardwareSn,
            DeviceControlRequest request) {
        return control(operationUid, hardwareSn, request, "RETIRED");
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public DeviceAssetView reevaluateAcceptance(
            UUID operationUid,
            String hardwareSn) {
        Scope scope = authorize(true, null, null, "device.manage");
        String normalizedHardwareSn = normalizeHardwareSn(hardwareSn);
        return command(
                operationUid,
                scope,
                "device.acceptance.reevaluate",
                "DEVICE_ASSET",
                normalizedHardwareSn,
                Map.of(),
                DeviceAssetView.class,
                () -> reevaluateAcceptance(normalizedHardwareSn));
    }

    @Transactional(readOnly = true)
    public List<AcceptanceEvidenceView> acceptanceEvidence(
            String hardwareSn) {
        authorize(true, null, null, "device.read");
        Asset asset = asset(normalizeHardwareSn(hardwareSn), false);
        return jdbc.query("""
                        SELECT evidence_uid, evidence_schema_version,
                               edge_software_version, edge_protocol_version,
                               onenet_online, persistent_store_healthy,
                               trusted_time_healthy,
                               configuration_persistence_healthy,
                               mcu_communication_healthy, sensors_healthy,
                               cameras_capture_healthy,
                               camera_upload_healthy, mcu_simulated,
                               cameras_simulated, evaluation_status,
                               failure_reasons_json,
                               LOWER(HEX(evidence_sha256)) evidence_sha256,
                               observed_at, received_at
                        FROM dev_device_acceptance_evidence
                        WHERE asset_id = ?
                        ORDER BY received_at DESC, id DESC
                        """,
                (rs, ignored) -> evidence(rs),
                asset.id());
    }

    @Transactional(readOnly = true)
    public CursorPage<ConfigurationVersionSummary> configurationVersions(
            String organizationCode,
            String deviceCode,
            Long beforeVersionNo,
            int requestedLimit) {
        if (beforeVersionNo != null && beforeVersionNo < 1) {
            throw invalid("beforeVersionNo 必须大于零");
        }
        Scope scope = authorize(
                false, null, organizationCode, "device.read");
        Asset asset = organizationAsset(
                scope, normalizeDeviceCode(deviceCode), false);
        int limit = requestedLimit <= 0
                ? DEFAULT_CURSOR_LIMIT
                : Math.min(requestedLimit, MAX_CURSOR_LIMIT);
        String beforePredicate = beforeVersionNo == null
                ? "" : " AND config.version_no < ?";
        List<Object> parameters = new ArrayList<>();
        parameters.add(scope.tenantId());
        parameters.add(scope.organizationId());
        parameters.add(asset.id());
        if (beforeVersionNo != null) {
            parameters.add(beforeVersionNo);
        }
        parameters.add(limit + 1);
        List<ConfigurationVersionRow> rows = jdbc.query(
                configurationSelect() + """
                        WHERE config.tenant_id = ?
                          AND config.organization_id = ?
                          AND config.asset_id = ?
                        """ + beforePredicate
                        + " ORDER BY config.version_no DESC LIMIT ?",
                (rs, ignored) -> configurationVersionRow(rs),
                parameters.toArray());
        boolean hasMore = rows.size() > limit;
        List<ConfigurationVersionRow> visible = hasMore
                ? rows.subList(0, limit) : rows;
        Long next = hasMore && !visible.isEmpty()
                ? visible.getLast().versionNo() : null;
        return new CursorPage<>(visible.stream()
                .map(this::configurationSummary).toList(), next);
    }

    @Transactional(readOnly = true)
    public ConfigurationVersionView configurationVersion(
            String organizationCode,
            String deviceCode,
            long versionNo) {
        if (versionNo < 1) {
            throw notFound();
        }
        Scope scope = authorize(
                false, null, organizationCode, "device.read");
        Asset asset = organizationAsset(
                scope, normalizeDeviceCode(deviceCode), false);
        ConfigurationVersionRow configuration = findConfigurationVersion(
                scope, asset.id(), versionNo)
                .orElseThrow(TargetDeviceApplication::notFound);
        return configurationView(
                asset.devicePublicCode(),
                configuration,
                configurationPorts(scope, asset.id(), configuration.id()));
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public ConfigurationAcceptedView releaseConfiguration(
            UUID operationUid,
            String organizationCode,
            String deviceCode,
            ConfigurationReleaseRequest request) {
        Scope scope = authorize(false, null, organizationCode,
                "device.configuration.manage");
        requireEnabledScope(scope);
        if (request == null
                || request.expectedLatestVersion() == null
                || request.locationCorrectionConfirmed() == null) {
            throw invalid("配置发布请求不完整");
        }
        String code = normalizeDeviceCode(deviceCode);
        String statusBaseUrl = configurationApplicationCollectionUrl(
                organizationCode, code);
        return command(
                operationUid,
                scope,
                "device.configuration.release",
                "DEVICE_CONFIGURATION",
                code,
                request,
                ConfigurationAcceptedView.class,
                () -> releaseConfiguration(
                        operationUid, scope, code, statusBaseUrl, request));
    }

    @Transactional(readOnly = true)
    public ConfigurationApplicationView configurationApplication(
            String organizationCode,
            String deviceCode,
            UUID applicationUid) {
        Scope scope = authorize(
                false, null, organizationCode, "device.read");
        Asset asset = organizationAsset(
                scope, normalizeDeviceCode(deviceCode), false);
        ApplicationRow application = findApplication(
                scope, asset.id(), applicationUid, false)
                .orElseThrow(TargetDeviceApplication::applicationNotFound);
        return applicationView(application);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public ConfigurationAcceptedView resynchronizeConfiguration(
            UUID operationUid,
            String organizationCode,
            String deviceCode,
            UUID applicationUid,
            ConfigurationResynchronizationRequest request) {
        Scope scope = authorize(false, null, organizationCode,
                "device.configuration.manage");
        requireEnabledScope(scope);
        if (request == null || request.expectedVersion() == null) {
            throw invalid("配置重同步请求不完整");
        }
        String code = normalizeDeviceCode(deviceCode);
        String statusUrl = configurationApplicationCollectionUrl(
                organizationCode, code) + "/" + applicationUid;
        return command(
                operationUid,
                scope,
                "device.configuration.resynchronize",
                CONFIGURATION_TARGET_TYPE,
                code + "|application:" + applicationUid,
                request,
                ConfigurationAcceptedView.class,
                () -> resynchronizeConfiguration(
                        operationUid,
                        scope,
                        code,
                        applicationUid,
                        statusUrl,
                        request));
    }

    private CommandResult<ConfigurationAcceptedView> releaseConfiguration(
            UUID operationUid,
            Scope scope,
            String deviceCode,
            String applicationBaseUrl,
            ConfigurationReleaseRequest request) {
        Asset asset = organizationAsset(scope, deviceCode, true);
        Long current = jdbc.queryForObject("""
                        SELECT COALESCE(MAX(version_no), 0)
                        FROM dev_config_version
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                        """,
                Long.class,
                scope.tenantId(),
                scope.organizationId(),
                asset.id());
        long latestVersion = current == null ? 0 : current;
        if (latestVersion != request.expectedLatestVersion()) {
            throw versionConflict(latestVersion);
        }
        var normalized = canonicalizer.normalize(
                request, asset.expectedPortCount());
        Optional<ConfigurationVersionRow> previous = latestVersion == 0
                ? Optional.empty()
                : findConfigurationVersion(scope, asset.id(), latestVersion);
        if (previous.isPresent()
                && previous.get().contentSha256()
                .equals(normalized.contentSha256Hex())) {
            throw unprocessable(
                    "DEVICE.CONFIGURATION_UNCHANGED",
                    "完整配置内容与当前最高版本相同");
        }
        if (previous.isPresent()
                && locationChanged(previous.get(), normalized.device())
                && !request.locationCorrectionConfirmed()) {
            throw unprocessable(
                    "DEVICE.LOCATION_CORRECTION_CONFIRMATION_REQUIRED",
                    "非首次修改地址或坐标必须明确确认位置纠正");
        }

        long versionNo = latestVersion + 1;
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
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, 'STAFF', ?, ?, ?
                )
                """,
                scope.tenantId(),
                scope.organizationId(),
                asset.id(),
                versionNo,
                DeviceConfigurationCanonicalizer.CONFIGURATION_SCHEMA_VERSION,
                normalized.device().displayName(),
                normalized.device().address(),
                nullableDecimal(normalized.device().latitude()),
                nullableDecimal(normalized.device().longitude()),
                normalized.device().edgeHeartbeatIntervalMs(),
                normalized.device().edgeHeartbeatMissThreshold(),
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
                scope.staffAccountId(),
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
                scope.tenantId(), scope.organizationId(), asset.id());
        if (portIds.size() != asset.expectedPortCount()) {
            throw invariant();
        }
        for (var port : normalized.ports()) {
            ConfigurationPortSnapshot view = port.view();
            Long portId = portIds.get(view.portNo());
            if (portId == null) {
                throw invariant();
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
                    scope.tenantId(),
                    scope.organizationId(),
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
                scope.tenantId(),
                scope.organizationId(),
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
        Map<String, Object> envelope = configurationCommandEnvelope(
                commandUid,
                asset.hardwareSn(),
                applicationUid,
                issuedAt,
                payload,
                payloadSha256);
        byte[] envelopeSha256 = canonicalizer.payloadSha256(envelope);
        long commandId = insertAndReturnKey("""
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
                    ?, ?, ?, ?, 'APPLY_CONFIGURATION',
                    NULL, NULL, ?, NULL, NULL,
                    2, CAST(? AS JSON), ?, 'QUEUED', ?,
                    NULL, NULL, NULL, 0, ?, ?
                )
                """,
                commandUid.toString(),
                scope.tenantId(),
                scope.organizationId(),
                asset.id(),
                applicationId,
                writeJson(envelope),
                envelopeSha256,
                now,
                now,
                now);
        Map<String, Object> taskSnapshot = configurationTaskSnapshot(
                commandUid,
                asset.hardwareSn(),
                applicationUid,
                envelopeSha256);
        taskRegistrationPort.register(new ReliableDeviceTaskRegistration(
                CONFIGURATION_TASK_TYPE,
                CONFIGURATION_TASK_TYPE + ":"
                        + applicationUid.toString().toUpperCase(Locale.ROOT),
                CONFIGURATION_TARGET_TYPE,
                applicationUid.toString(),
                taskRefFactory.issue(
                        scope.tenantId(),
                        scope.organizationId(),
                        asset.id(),
                        commandId),
                2,
                writeJson(taskSnapshot),
                envelopeSha256,
                operationUid,
                null,
                12,
                true,
                now));

        ConfigurationAcceptedView response = new ConfigurationAcceptedView(
                operationUid,
                applicationUid,
                applicationUid,
                versionNo,
                normalized.contentSha256Hex(),
                canonicalizer.hex(mcuPayloadSha256),
                "PENDING",
                "PENDING",
                applicationBaseUrl + "/" + applicationUid,
                RECOMMENDED_POLL_AFTER_MS);
        Map<String, Object> after = new LinkedHashMap<>();
        after.put("versionNo", versionNo);
        after.put("applicationUid", applicationUid.toString());
        after.put("contentSha256", normalized.contentSha256Hex());
        after.put("status", "PENDING");
        return new CommandResult<>(
                response,
                Map.of("latestVersion", latestVersion),
                after,
                request.reason());
    }

    private CommandResult<ConfigurationAcceptedView>
            resynchronizeConfiguration(
                    UUID operationUid,
                    Scope scope,
                    String deviceCode,
                    UUID applicationUid,
                    String statusUrl,
                    ConfigurationResynchronizationRequest request) {
        Asset asset = organizationAsset(scope, deviceCode, true);
        ApplicationRow application = findApplication(
                scope, asset.id(), applicationUid, true)
                .orElseThrow(TargetDeviceApplication::applicationNotFound);
        if (application.version() != request.expectedVersion()) {
            throw versionConflict(application.version());
        }
        if (application.versionNo() != application.latestVersionNo()) {
            throw conflict(
                    "DEVICE.CONFIGURATION_APPLICATION_SUPERSEDED",
                    "配置应用已经被更高版本取代");
        }
        if ("APPLIED".equals(application.status())) {
            throw conflict(
                    "DEVICE.CONFIGURATION_ALREADY_APPLIED",
                    "配置已经精确应用");
        }
        ReliableDeviceTaskStatus task = taskStatusPort.find(
                        CONFIGURATION_TASK_TYPE,
                        CONFIGURATION_TARGET_TYPE,
                        applicationUid.toString(),
                        true)
                .orElseThrow(TargetDeviceApplication::invariant);
        boolean recoverable = "FAILED".equals(application.status())
                || "BLOCKED".equals(task.state());
        if (!recoverable) {
            throw conflict(
                    "DEVICE.CONFIGURATION_RESYNC_NOT_ALLOWED",
                    "配置仍在自动重试，当前不需要人工重同步");
        }
        LocalDateTime now = databaseNow();
        requireSingle(jdbc.update("""
                        UPDATE dev_config_application
                        SET status = CASE
                                WHEN status = 'FAILED' THEN 'PENDING'
                                ELSE status
                            END,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                        """,
                now,
                application.id()));
        taskWakePort.wake(new ReliableTaskWake(
                task.taskUid(), "WEB_RESYNCHRONIZATION"));
        ApplicationRow after = findApplication(
                scope, asset.id(), applicationUid, false)
                .orElseThrow(TargetDeviceApplication::invariant);
        ReliableDeviceTaskStatus afterTask = taskStatusPort.find(
                        CONFIGURATION_TASK_TYPE,
                        CONFIGURATION_TARGET_TYPE,
                        applicationUid.toString(),
                        false)
                .orElseThrow(TargetDeviceApplication::invariant);
        ConfigurationAcceptedView response = new ConfigurationAcceptedView(
                operationUid,
                applicationUid,
                applicationUid,
                after.versionNo(),
                after.contentSha256(),
                after.mcuPayloadSha256(),
                after.status(),
                afterTask.state(),
                statusUrl,
                RECOMMENDED_POLL_AFTER_MS);
        return new CommandResult<>(
                response,
                Map.of(
                        "applicationVersion", application.version(),
                        "status", application.status(),
                        "dispatchState", task.state()),
                Map.of(
                        "applicationVersion", after.version(),
                        "status", after.status(),
                        "dispatchState", afterTask.state()),
                request.reason());
    }

    private PageData<DeviceAssetView> listAssets(
            Long tenantId,
            Long organizationId,
            boolean hideUnavailable,
            int requestedPage,
            int requestedPageSize,
            String hardwareSn,
            String lifecycleStatus,
            String acceptanceStatus) {
        int page = Math.max(1, requestedPage);
        int pageSize = requestedPageSize < 1
                ? 20 : Math.min(requestedPageSize, MAX_PAGE_SIZE);
        StringBuilder from = new StringBuilder(assetFrom());
        from.append(" WHERE 1 = 1");
        List<Object> args = new ArrayList<>();
        if (tenantId != null) {
            from.append(" AND asset.tenant_id = ?");
            args.add(tenantId);
        }
        if (organizationId != null) {
            from.append(" AND asset.organization_id = ?");
            args.add(organizationId);
        }
        if (hideUnavailable) {
            from.append(" AND asset.lifecycle_status = 'NORMAL'");
        } else if (blankToNull(lifecycleStatus) != null) {
            from.append(" AND asset.lifecycle_status = ?");
            args.add(lifecycleStatus.trim().toUpperCase());
        }
        if (blankToNull(acceptanceStatus) != null) {
            from.append(" AND asset.acceptance_status = ?");
            args.add(acceptanceStatus.trim().toUpperCase());
        }
        if (blankToNull(hardwareSn) != null) {
            from.append(" AND asset.hardware_sn LIKE ? ESCAPE '\\\\'");
            args.add("%" + escapeLike(hardwareSn.trim()) + "%");
        }
        Long total = jdbc.queryForObject(
                "SELECT COUNT(*) " + from, Long.class, args.toArray());
        List<Object> pageArgs = new ArrayList<>(args);
        pageArgs.add(pageSize);
        pageArgs.add((long) (page - 1) * pageSize);
        List<DeviceAssetView> items = jdbc.query(
                assetSelect() + from
                        + " ORDER BY asset.id DESC LIMIT ? OFFSET ?",
                (rs, ignored) -> assetView(rs),
                pageArgs.toArray());
        return new PageData<>(items, page, pageSize, total == null ? 0 : total);
    }

    private CommandResult<DeviceAssetView> createAsset(
            NormalizedAssetCreate request) {
        if (findAssetView(request.hardwareSn(), null, null, false).isPresent()) {
            throw conflict(
                    "DEVICE.ASSET_ALREADY_EXISTS",
                    "硬件序列号已经登记");
        }
        LocalDateTime now = databaseNow();
        UUID assetUid = UUID.randomUUID();
        String publicCode = randomPublicCode();
        try {
            jdbc.update("""
                            INSERT INTO dev_device_asset (
                                asset_uid, device_public_code, hardware_sn,
                                model_name, production_batch,
                                expected_port_count,
                                tenant_id, tenant_assigned_at,
                                organization_id, organization_assigned_at,
                                acceptance_status, accepted_at,
                                acceptance_evidence_sha256,
                                last_acceptance_evaluated_at,
                                acceptance_failure_json,
                                miniapp_qr_status, miniapp_qr_object_key,
                                miniapp_qr_generated_at,
                                lifecycle_status, disabled_at,
                                disable_reason, retired_at,
                                retirement_reason, control_version,
                                created_at, updated_at
                            ) VALUES (
                                ?, ?, ?, ?, ?, ?,
                                NULL, NULL, NULL, NULL,
                                'PENDING', NULL, NULL, NULL, NULL,
                                'NOT_ASSIGNED', NULL, NULL,
                                'NORMAL', NULL, NULL, NULL, NULL, 0, ?, ?
                            )
                            """,
                    assetUid.toString(),
                    publicCode,
                    request.hardwareSn(),
                    request.modelCode(),
                    request.productionBatch(),
                    request.expectedPortCount(),
                    now,
                    now);
        } catch (DataIntegrityViolationException exception) {
            throw conflict(
                    "DEVICE.ASSET_ALREADY_EXISTS",
                    "设备硬件序列号、公开码或厂家袋码重复");
        }
        Asset asset = asset(request.hardwareSn(), true);
        jdbc.update("""
                        INSERT INTO dev_device_transport_state (
                            asset_id, onenet_connection_status,
                            status_observed_at, status_received_at,
                            evidence_source, source_inbox_id,
                            lock_version, created_at, updated_at
                        ) VALUES (?, 'UNKNOWN', NULL, NULL, NULL, NULL, 0, ?, ?)
                        """,
                asset.id(), now, now);
        for (FactoryBag bag : request.factoryBags()) {
            jdbc.update("""
                            INSERT INTO dev_factory_installed_bag (
                                asset_id, port_no, bag_code, tare_status,
                                last_failure_code, installed_at,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, 'PENDING', NULL, ?, ?, ?)
                            """,
                    asset.id(), bag.portNo(), bag.bagCode(), now, now, now);
        }
        DeviceAssetView response = platformView(request.hardwareSn());
        return new CommandResult<>(
                response,
                Map.of(),
                snapshot(response),
                null);
    }

    private CommandResult<DeviceAssetView> assignTenant(
            String hardwareSn,
            String tenantCode,
            long expectedVersion) {
        Asset asset = asset(hardwareSn, true);
        Tenant tenant = jdbc.query("""
                        SELECT id, tenant_code, status
                        FROM iam_tenant
                        WHERE tenant_code = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new Tenant(
                        rs.getLong("id"),
                        rs.getString("tenant_code"),
                        rs.getString("status")),
                tenantCode).stream().findFirst()
                .orElseThrow(TargetDeviceApplication::notFound);
        if (asset.tenantId() != null) {
            if (asset.tenantId() == tenant.id()) {
                return unchanged(platformView(hardwareSn));
            }
            throw conflict(
                    "DEVICE.TENANT_ASSIGNMENT_PERMANENT",
                    "设备已经永久分配给其他租户，不能修改");
        }
        requireVersion(asset.controlVersion(), expectedVersion);
        if (!"NORMAL".equals(asset.lifecycleStatus())) {
            throw conflict("DEVICE.ASSET_UNAVAILABLE", "禁用或报废设备不能分配");
        }
        if (!"PASSED".equals(asset.acceptanceStatus())) {
            throw unprocessable(
                    "DEVICE.ACCEPTANCE_REQUIRED",
                    "真实机器尚未自动通过平台验收");
        }
        if (!"ENABLED".equals(tenant.status())) {
            throw unprocessable("TENANT.DISABLED", "目标租户当前已禁用");
        }
        LocalDateTime now = databaseNow();
        requireSingle(jdbc.update("""
                        UPDATE dev_device_asset
                        SET tenant_id = ?, tenant_assigned_at = ?,
                            control_version = control_version + 1,
                            updated_at = ?
                        WHERE id = ? AND tenant_id IS NULL
                          AND control_version = ?
                        """,
                tenant.id(), now, now, asset.id(), expectedVersion));
        DeviceAssetView response = platformView(hardwareSn);
        return changed(asset, response, null);
    }

    private CommandResult<DeviceAssetView> assignOrganization(
            UUID operationUid,
            Scope scope,
            String hardwareSn,
            String organizationCode,
            long expectedVersion) {
        Asset asset = asset(hardwareSn, true);
        if (!Objects.equals(asset.tenantId(), scope.tenantId())) {
            throw notFound();
        }
        Organization organization = jdbc.query("""
                        SELECT id, organization_code, status
                        FROM iam_organization
                        WHERE tenant_id = ? AND organization_code = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new Organization(
                        rs.getLong("id"),
                        rs.getString("organization_code"),
                        rs.getString("status")),
                scope.tenantId(), organizationCode).stream().findFirst()
                .orElseThrow(TargetDeviceApplication::notFound);
        if (asset.organizationId() != null) {
            if (asset.organizationId() == organization.id()) {
                return unchanged(tenantView(hardwareSn, scope.tenantId()));
            }
            throw conflict(
                    "DEVICE.ORGANIZATION_ASSIGNMENT_PERMANENT",
                    "设备已经永久分配给其他机构，不能修改");
        }
        requireVersion(asset.controlVersion(), expectedVersion);
        if (!"NORMAL".equals(asset.lifecycleStatus())
                || !"PASSED".equals(asset.acceptanceStatus())) {
            throw unprocessable(
                    "DEVICE.ASSET_UNAVAILABLE",
                    "设备未通过验收、已禁用或已报废");
        }
        if (!"ENABLED".equals(organization.status())) {
            throw unprocessable("ORGANIZATION.DISABLED", "目标机构当前已禁用");
        }
        List<FactoryBag> bags = factoryBags(asset.id());
        if (bags.size() != asset.expectedPortCount()) {
            throw unprocessable(
                    "DEVICE.FACTORY_BAGS_INCOMPLETE",
                    "厂家必须为每个投口登记一个初始袋");
        }
        LocalDateTime now = databaseNow();
        requireSingle(jdbc.update("""
                        UPDATE dev_device_asset
                        SET organization_id = ?, organization_assigned_at = ?,
                            miniapp_qr_status = 'PENDING',
                            control_version = control_version + 1,
                            updated_at = ?
                        WHERE id = ? AND organization_id IS NULL
                          AND control_version = ?
                        """,
                organization.id(), now, now, asset.id(), expectedVersion));
        initializeOrganizationFacts(
                asset,
                scope.tenantId(),
                organization.id(),
                bags,
                now);
        activationService.reconcileInCurrentTransaction(
                asset.id(), operationUid);
        DeviceAssetView response = tenantView(hardwareSn, scope.tenantId());
        return changed(asset, response, null);
    }

    private void initializeOrganizationFacts(
            Asset asset,
            long tenantId,
            long organizationId,
            List<FactoryBag> bags,
            LocalDateTime now) {
        jdbc.update("""
                        INSERT INTO dev_device_runtime_state (
                            asset_id, tenant_id, organization_id,
                            edge_connection_status, mcu_link_status,
                            safety_status, aggregate_weight_health,
                            camera_health, local_storage_health,
                            clock_sync_health, edge_boot_id,
                            edge_software_version, mcu_firmware_version,
                            mcu_boot_id, uart_state, uart_protocol_major,
                            uart_protocol_minor, capability_bitmap_hex,
                            last_mcu_reset_reason,
                            applied_config_version_no,
                            applied_config_content_sha256,
                            applied_mcu_payload_sha256,
                            local_storage_state, clock_state,
                            pending_reliable_event_count,
                            last_heartbeat_at, last_device_event_at,
                            lock_version, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, 'UNKNOWN', 'UNKNOWN',
                            'UNKNOWN', 'UNKNOWN', 'UNKNOWN', 'UNKNOWN',
                            'UNKNOWN', NULL, NULL, NULL, NULL,
                            NULL, NULL, NULL, NULL, NULL,
                            NULL, NULL, NULL, NULL, NULL, NULL,
                            NULL, NULL, 0, ?, ?
                        )
                        """,
                asset.id(), tenantId, organizationId, now, now);
        Map<Integer, FactoryBag> byPort = new LinkedHashMap<>();
        bags.forEach(bag -> byPort.put(bag.portNo(), bag));
        for (int portNo = 1; portNo <= asset.expectedPortCount(); portNo++) {
            long portId = insertAndReturnKey("""
                    INSERT INTO dev_port (
                        tenant_id, organization_id, asset_id,
                        port_no, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """, tenantId, organizationId, asset.id(), portNo, now);
            jdbc.update("""
                            INSERT INTO dev_port_runtime_state (
                                port_id, tenant_id, organization_id, asset_id,
                                delivery_door_state,
                                delivery_door_actuator_health,
                                delivery_door_contact_state,
                                clean_lock_power_state,
                                clean_solenoid_health,
                                clean_door_inferred_state,
                                clean_door_state_basis,
                                weight_sensor_health, infrared_value,
                                infrared_sensor_health, smoke_state,
                                smoke_sensor_health, safety_status,
                                pending_delivery_result_session_id,
                                last_observed_at, lock_version,
                                created_at, updated_at
                            ) VALUES (
                                ?, ?, ?, ?, 'UNKNOWN', 'UNKNOWN', 'UNKNOWN',
                                'UNKNOWN', 'UNKNOWN', 'UNKNOWN',
                                'INFERRED_FROM_LOCK_POWER',
                                'UNKNOWN', 'UNKNOWN', 'UNKNOWN',
                                'UNKNOWN', 'UNKNOWN', 'UNKNOWN',
                                NULL, NULL, 0, ?, ?
                            )
                            """,
                    portId, tenantId, organizationId, asset.id(), now, now);
            FactoryBag factoryBag = byPort.get(portNo);
            UUID bagUid = UUID.randomUUID();
            long bagId = insertAndReturnKey("""
                    INSERT INTO rec_bag (
                        bag_uid, tenant_id, organization_id,
                        bag_code, registered_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """, bagUid.toString(), tenantId, organizationId,
                    factoryBag.bagCode(), now, now);
            jdbc.update("""
                            INSERT INTO rec_bag_current_occupancy (
                                bag_id, tenant_id, organization_id,
                                occupancy_type, port_id,
                                clean_operation_id, acquired_at
                            ) VALUES (?, ?, ?, 'PORT_BOUND', ?, NULL, ?)
                            """,
                    bagId, tenantId, organizationId, portId, now);
            jdbc.update("""
                            INSERT INTO rec_bag_occupancy_event (
                                event_uid, tenant_id, organization_id,
                                bag_id, port_id, clean_operation_id,
                                event_type, occurred_at, created_at
                            ) VALUES (?, ?, ?, ?, ?, NULL,
                                      'INITIAL_INSTALLED', ?, ?)
                            """,
                    UUID.randomUUID().toString(), tenantId, organizationId,
                    bagId, portId, now, now);
            jdbc.update("""
                            INSERT INTO rec_port_capacity_state (
                                port_id, tenant_id, organization_id, asset_id,
                                baseline_state, current_baseline_id,
                                current_baseline_weight_g,
                                latest_stable_total_weight_g,
                                raw_net_weight_g,
                                displayed_fullness_percent,
                                detection_gate, current_detection_id,
                                current_rule_fingerprint,
                                confirmed_fullness_state,
                                last_detection_id, current_fullness_event_id,
                                current_bag_id,
                                current_fullness_state_change_id,
                                last_fullness_edge_event_id,
                                last_fullness_edge_event_sequence,
                                last_fullness_reported_at,
                                lock_version, updated_at
                            ) VALUES (
                                ?, ?, ?, ?, 'UNINITIALIZED', NULL, NULL,
                                NULL, NULL, NULL, 'UNKNOWN', NULL, NULL,
                                'UNKNOWN', NULL, NULL, ?, NULL,
                                NULL, NULL, NULL, 0, ?
                            )
                            """,
                    portId, tenantId, organizationId, asset.id(), bagId, now);
        }
    }

    private DeviceAssetView control(
            UUID operationUid,
            String hardwareSn,
            DeviceControlRequest request,
            String targetStatus) {
        Scope scope = authorize(true, null, null, "device.manage");
        String normalizedHardwareSn = normalizeHardwareSn(hardwareSn);
        if (request == null || request.expectedVersion() == null) {
            throw invalid("设备控制请求不完整");
        }
        String reason = required(request.reason(), 500, "reason");
        Map<String, Object> normalized = Map.of(
                "expectedVersion", request.expectedVersion(),
                "reason", reason,
                "targetStatus", targetStatus);
        return command(
                operationUid,
                scope,
                "device.asset." + targetStatus.toLowerCase(),
                "DEVICE_ASSET",
                normalizedHardwareSn,
                normalized,
                DeviceAssetView.class,
                () -> control(
                        normalizedHardwareSn,
                        request.expectedVersion(),
                        reason,
                        targetStatus));
    }

    private CommandResult<DeviceAssetView> control(
            String hardwareSn,
            long expectedVersion,
            String reason,
            String targetStatus) {
        Asset before = asset(hardwareSn, true);
        if (before.lifecycleStatus().equals(targetStatus)) {
            return unchanged(platformView(hardwareSn));
        }
        if ("RETIRED".equals(before.lifecycleStatus())) {
            throw conflict(
                    "DEVICE.RETIRED_IS_FINAL",
                    "报废是最终状态，不能恢复或再次禁用");
        }
        requireVersion(before.controlVersion(), expectedVersion);
        LocalDateTime now = databaseNow();
        int updated;
        if ("DISABLED".equals(targetStatus)) {
            if (!"NORMAL".equals(before.lifecycleStatus())) {
                throw conflict("DEVICE.INVALID_LIFECYCLE", "设备当前不能禁用");
            }
            updated = jdbc.update("""
                            UPDATE dev_device_asset
                            SET lifecycle_status = 'DISABLED',
                                disabled_at = ?, disable_reason = ?,
                                control_version = control_version + 1,
                                updated_at = ?
                            WHERE id = ? AND lifecycle_status = 'NORMAL'
                              AND control_version = ?
                            """,
                    now, reason, now, before.id(), expectedVersion);
        } else if ("NORMAL".equals(targetStatus)) {
            if (!"DISABLED".equals(before.lifecycleStatus())) {
                throw conflict("DEVICE.INVALID_LIFECYCLE", "设备当前不能恢复");
            }
            updated = jdbc.update("""
                            UPDATE dev_device_asset
                            SET lifecycle_status = 'NORMAL',
                                disabled_at = NULL, disable_reason = NULL,
                                control_version = control_version + 1,
                                updated_at = ?
                            WHERE id = ? AND lifecycle_status = 'DISABLED'
                              AND control_version = ?
                            """,
                    now, before.id(), expectedVersion);
        } else if ("RETIRED".equals(targetStatus)) {
            updated = jdbc.update("""
                            UPDATE dev_device_asset
                            SET lifecycle_status = 'RETIRED',
                                retired_at = ?, retirement_reason = ?,
                                control_version = control_version + 1,
                                updated_at = ?
                            WHERE id = ?
                              AND lifecycle_status IN ('NORMAL', 'DISABLED')
                              AND control_version = ?
                            """,
                    now, reason, now, before.id(), expectedVersion);
        } else {
            throw new IllegalArgumentException("unknown device target status");
        }
        requireSingle(updated);
        DeviceAssetView response = platformView(hardwareSn);
        return changed(before, response, reason);
    }

    private CommandResult<DeviceAssetView> reevaluateAcceptance(
            String hardwareSn) {
        Asset asset = asset(hardwareSn, true);
        EvidenceDecision evidence = jdbc.query("""
                        SELECT evaluation_status, evidence_sha256,
                               failure_reasons_json, received_at
                        FROM dev_device_acceptance_evidence
                        WHERE asset_id = ?
                        ORDER BY received_at DESC, id DESC
                        LIMIT 1
                        FOR UPDATE
                        """,
                (rs, ignored) -> new EvidenceDecision(
                        rs.getString("evaluation_status"),
                        rs.getBytes("evidence_sha256"),
                        rs.getString("failure_reasons_json"),
                        rs.getObject("received_at", LocalDateTime.class)),
                asset.id()).stream().findFirst().orElse(null);
        LocalDateTime now = databaseNow();
        if (evidence == null) {
            if (!"PASSED".equals(asset.acceptanceStatus())) {
                jdbc.update("""
                                UPDATE dev_device_asset
                                SET acceptance_status = 'FAILED',
                                    accepted_at = NULL,
                                    acceptance_evidence_sha256 = NULL,
                                    last_acceptance_evaluated_at = ?,
                                    acceptance_failure_json =
                                        JSON_ARRAY('ACCEPTANCE_EVIDENCE_MISSING'),
                                    control_version = control_version + 1,
                                    updated_at = ?
                                WHERE id = ?
                                """,
                        now, now, asset.id());
            }
        } else if ("PASSED".equals(evidence.status())) {
            jdbc.update("""
                            UPDATE dev_device_asset
                            SET acceptance_status = 'PASSED',
                                accepted_at = COALESCE(accepted_at, ?),
                                acceptance_evidence_sha256 = ?,
                                last_acceptance_evaluated_at = ?,
                                acceptance_failure_json = NULL,
                                control_version = control_version + 1,
                                updated_at = ?
                            WHERE id = ?
                            """,
                    evidence.receivedAt(), evidence.sha256(), now, now, asset.id());
        } else if (!"PASSED".equals(asset.acceptanceStatus())) {
            jdbc.update("""
                            UPDATE dev_device_asset
                            SET acceptance_status = 'FAILED',
                                accepted_at = NULL,
                                acceptance_evidence_sha256 = ?,
                                last_acceptance_evaluated_at = ?,
                                acceptance_failure_json = CAST(? AS JSON),
                                control_version = control_version + 1,
                                updated_at = ?
                            WHERE id = ?
                            """,
                    evidence.sha256(), now, evidence.failureReasonsJson(),
                    now, asset.id());
        } else {
            jdbc.update("""
                            UPDATE dev_device_asset
                            SET last_acceptance_evaluated_at = ?, updated_at = ?
                            WHERE id = ?
                            """, now, now, asset.id());
        }
        DeviceAssetView response = platformView(hardwareSn);
        return changed(asset, response, null);
    }

    private Asset organizationAsset(
            Scope scope, String deviceCode, boolean lock) {
        return jdbc.query("""
                        SELECT id, hardware_sn, device_public_code,
                               model_name, expected_port_count,
                               tenant_id, organization_id,
                               acceptance_status, lifecycle_status,
                               control_version
                        FROM dev_device_asset
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND device_public_code = ?
                          AND lifecycle_status = 'NORMAL'
                        """ + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> new Asset(
                        rs.getLong("id"),
                        rs.getString("hardware_sn"),
                        rs.getString("device_public_code"),
                        rs.getString("model_name"),
                        rs.getInt("expected_port_count"),
                        nullableLong(rs, "tenant_id"),
                        nullableLong(rs, "organization_id"),
                        rs.getString("acceptance_status"),
                        rs.getString("lifecycle_status"),
                        rs.getLong("control_version")),
                scope.tenantId(),
                scope.organizationId(),
                deviceCode).stream().findFirst()
                .orElseThrow(TargetDeviceApplication::notFound);
    }

    private static String configurationSelect() {
        return """
                SELECT config.id, config.version_no,
                       config.schema_version,
                       config.device_display_name,
                       config.location_address,
                       config.latitude, config.longitude,
                       config.edge_heartbeat_interval_ms,
                       config.edge_heartbeat_miss_threshold,
                       config.mcu_heartbeat_interval_ms,
                       config.mcu_heartbeat_miss_threshold,
                       config.door_close_retry_limit,
                       config.continue_delivery_wait_ms,
                       config.negative_weight_threshold_g,
                       config.delivery_auto_close_ms,
                       config.weight_measurement_timeout_ms,
                       config.delivery_door_travel_wait_ms,
                       config.clean_solenoid_pulse_ms,
                       config.smoke_monitoring_enabled,
                       LOWER(HEX(config.content_sha256)) content_sha256,
                       LOWER(HEX(config.mcu_payload_sha256))
                           mcu_payload_sha256,
                       config.publication_source,
                       staff.display_name publisher_name,
                       config.published_at,
                       app.application_uid,
                       app.status app_status,
                       app.lock_version app_version
                FROM dev_config_version config
                LEFT JOIN iam_staff_account staff
                  ON staff.tenant_id = config.tenant_id
                 AND staff.id = config.published_by_staff_account_id
                JOIN dev_config_application app
                  ON app.tenant_id = config.tenant_id
                 AND app.organization_id = config.organization_id
                 AND app.asset_id = config.asset_id
                 AND app.config_version_id = config.id
                """;
    }

    private Optional<ConfigurationVersionRow> findConfigurationVersion(
            Scope scope, long assetId, long versionNo) {
        return jdbc.query(configurationSelect() + """
                        WHERE config.tenant_id = ?
                          AND config.organization_id = ?
                          AND config.asset_id = ?
                          AND config.version_no = ?
                        """,
                (rs, ignored) -> configurationVersionRow(rs),
                scope.tenantId(),
                scope.organizationId(),
                assetId,
                versionNo).stream().findFirst();
    }

    private static ConfigurationVersionRow configurationVersionRow(
            ResultSet rs) throws SQLException {
        return new ConfigurationVersionRow(
                rs.getLong("id"),
                rs.getLong("version_no"),
                rs.getInt("schema_version"),
                rs.getString("device_display_name"),
                rs.getString("location_address"),
                decimalString(rs.getBigDecimal("longitude"), 7),
                decimalString(rs.getBigDecimal("latitude"), 7),
                rs.getLong("edge_heartbeat_interval_ms"),
                rs.getLong("edge_heartbeat_miss_threshold"),
                rs.getLong("mcu_heartbeat_interval_ms"),
                rs.getLong("mcu_heartbeat_miss_threshold"),
                rs.getLong("door_close_retry_limit"),
                rs.getLong("continue_delivery_wait_ms"),
                rs.getLong("negative_weight_threshold_g"),
                rs.getLong("delivery_auto_close_ms"),
                rs.getLong("weight_measurement_timeout_ms"),
                rs.getLong("delivery_door_travel_wait_ms"),
                rs.getLong("clean_solenoid_pulse_ms"),
                rs.getBoolean("smoke_monitoring_enabled"),
                rs.getString("content_sha256"),
                rs.getString("mcu_payload_sha256"),
                rs.getString("publication_source"),
                rs.getString("publisher_name"),
                instant(rs, "published_at"),
                UUID.fromString(rs.getString("application_uid")),
                rs.getString("app_status"),
                rs.getLong("app_version"));
    }

    private List<ConfigurationPortSnapshot> configurationPorts(
            Scope scope, long assetId, long configurationId) {
        return jdbc.query("""
                        SELECT port.port_no, snapshot.display_name,
                               snapshot.business_enabled,
                               snapshot.unit_price_yuan_per_kg,
                               snapshot.fullness_mode,
                               snapshot.configured_full_weight_g,
                               snapshot.delivery_settle_delay_ms,
                               snapshot.fullness_settle_wait_ms,
                               snapshot.fullness_confirmation_wait_ms,
                               snapshot.door_auto_close_timeout_ms,
                               snapshot.fullness_sensor_kind,
                               snapshot.fullness_distance_threshold_mm,
                               snapshot.fullness_sample_count,
                               snapshot.fullness_min_valid_sample_count,
                               snapshot.fullness_echo_timeout_us,
                               snapshot.weight_stable_window_ms,
                               snapshot.weight_maximum_fluctuation_g,
                               snapshot.weight_required_sample_count,
                               snapshot.weight_measurement_timeout_ms,
                               snapshot.weight_minimum_g,
                               snapshot.weight_maximum_g,
                               snapshot.calibration_version,
                               snapshot.infrared_sample_timeout_ms,
                               snapshot.delivery_door_operation_timeout_ms
                        FROM dev_port_config_snapshot snapshot
                        JOIN dev_port port
                          ON port.tenant_id = snapshot.tenant_id
                         AND port.organization_id = snapshot.organization_id
                         AND port.asset_id = snapshot.asset_id
                         AND port.id = snapshot.port_id
                        WHERE snapshot.tenant_id = ?
                          AND snapshot.organization_id = ?
                          AND snapshot.asset_id = ?
                          AND snapshot.config_version_id = ?
                        ORDER BY port.port_no
                        """,
                (rs, ignored) -> new ConfigurationPortSnapshot(
                        rs.getInt("port_no"),
                        rs.getString("display_name"),
                        rs.getBoolean("business_enabled"),
                        decimalString(rs.getBigDecimal(
                                "unit_price_yuan_per_kg"), 4),
                        rs.getString("fullness_mode"),
                        decimalString(BigDecimal.valueOf(
                                rs.getLong("configured_full_weight_g"), 3), 3),
                        rs.getLong("delivery_settle_delay_ms"),
                        rs.getLong("fullness_settle_wait_ms"),
                        rs.getLong("fullness_confirmation_wait_ms"),
                        rs.getLong("door_auto_close_timeout_ms"),
                        rs.getString("fullness_sensor_kind"),
                        rs.getLong("fullness_distance_threshold_mm"),
                        rs.getInt("fullness_sample_count"),
                        rs.getInt("fullness_min_valid_sample_count"),
                        rs.getLong("fullness_echo_timeout_us"),
                        rs.getLong("weight_stable_window_ms"),
                        rs.getLong("weight_maximum_fluctuation_g"),
                        rs.getInt("weight_required_sample_count"),
                        rs.getLong("weight_measurement_timeout_ms"),
                        rs.getLong("weight_minimum_g"),
                        rs.getLong("weight_maximum_g"),
                        rs.getLong("calibration_version"),
                        rs.getLong("infrared_sample_timeout_ms"),
                        rs.getLong("delivery_door_operation_timeout_ms")),
                scope.tenantId(),
                scope.organizationId(),
                assetId,
                configurationId);
    }

    private Optional<ApplicationRow> findApplication(
            Scope scope,
            long assetId,
            UUID applicationUid,
            boolean lock) {
        return jdbc.query("""
                        SELECT app.id, app.application_uid,
                               app.status, app.reported_version_no,
                               LOWER(HEX(app.reported_content_sha256))
                                   reported_content_sha256,
                               LOWER(HEX(app.reported_mcu_payload_sha256))
                                   reported_mcu_payload_sha256,
                               app.edge_persisted_at, app.mcu_synced_at,
                               app.applied_at, app.last_failure_at,
                               app.last_failure_code, app.lock_version,
                               config.version_no,
                               LOWER(HEX(config.content_sha256)) content_sha256,
                               LOWER(HEX(config.mcu_payload_sha256))
                                   mcu_payload_sha256,
                               (
                                   SELECT MAX(latest.version_no)
                                   FROM dev_config_version latest
                                   WHERE latest.tenant_id = app.tenant_id
                                     AND latest.organization_id =
                                         app.organization_id
                                     AND latest.asset_id = app.asset_id
                               ) latest_version_no
                        FROM dev_config_application app
                        JOIN dev_config_version config
                          ON config.tenant_id = app.tenant_id
                         AND config.organization_id = app.organization_id
                         AND config.asset_id = app.asset_id
                         AND config.id = app.config_version_id
                        WHERE app.tenant_id = ?
                          AND app.organization_id = ?
                          AND app.asset_id = ?
                          AND app.application_uid = ?
                        """ + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> new ApplicationRow(
                        rs.getLong("id"),
                        UUID.fromString(rs.getString("application_uid")),
                        rs.getLong("version_no"),
                        rs.getLong("latest_version_no"),
                        rs.getString("content_sha256"),
                        rs.getString("mcu_payload_sha256"),
                        rs.getString("status"),
                        rs.getLong("lock_version"),
                        nullableLong(rs, "reported_version_no"),
                        rs.getString("reported_content_sha256"),
                        rs.getString("reported_mcu_payload_sha256"),
                        instant(rs, "edge_persisted_at"),
                        instant(rs, "mcu_synced_at"),
                        instant(rs, "applied_at"),
                        rs.getString("last_failure_code"),
                        instant(rs, "last_failure_at")),
                scope.tenantId(),
                scope.organizationId(),
                assetId,
                applicationUid.toString()).stream().findFirst();
    }

    private ConfigurationVersionSummary configurationSummary(
            ConfigurationVersionRow row) {
        return new ConfigurationVersionSummary(
                row.versionNo(),
                row.contentSha256(),
                row.mcuPayloadSha256(),
                row.deviceDisplayName(),
                row.publicationSource(),
                publisherName(row),
                row.publishedAt(),
                applicationSummary(row));
    }

    private ConfigurationVersionView configurationView(
            String deviceCode,
            ConfigurationVersionRow row,
            List<ConfigurationPortSnapshot> ports) {
        return new ConfigurationVersionView(
                deviceCode,
                row.versionNo(),
                row.schemaVersion(),
                row.contentSha256(),
                row.mcuPayloadSha256(),
                configurationDevice(row),
                ports,
                row.publicationSource(),
                publisherName(row),
                row.publishedAt(),
                applicationSummary(row));
    }

    private ConfigurationApplicationSummary applicationSummary(
            ConfigurationVersionRow row) {
        String dispatch = taskStatusPort.find(
                        CONFIGURATION_TASK_TYPE,
                        CONFIGURATION_TARGET_TYPE,
                        row.applicationUid().toString(),
                        false)
                .map(ReliableDeviceTaskStatus::state)
                .orElse("MISSING");
        return new ConfigurationApplicationSummary(
                row.applicationUid(),
                row.applicationStatus(),
                dispatch,
                row.applicationVersion());
    }

    private ConfigurationApplicationView applicationView(
            ApplicationRow application) {
        ReliableDeviceTaskStatus task = taskStatusPort.find(
                        CONFIGURATION_TASK_TYPE,
                        CONFIGURATION_TARGET_TYPE,
                        application.applicationUid().toString(),
                        false)
                .orElseThrow(TargetDeviceApplication::invariant);
        boolean latest = application.versionNo()
                == application.latestVersionNo();
        boolean superseded = !latest;
        List<String> nextActions;
        Long pollAfter;
        if (superseded) {
            nextActions = List.of("VIEW_LATEST_CONFIGURATION");
            pollAfter = null;
        } else if ("APPLIED".equals(application.status())) {
            nextActions = List.of();
            pollAfter = null;
        } else if ("BLOCKED".equals(task.state())) {
            nextActions = List.of("RESYNCHRONIZE");
            pollAfter = null;
        } else if ("FAILED".equals(application.status())) {
            nextActions = application.lastFailureCode() != null
                    && application.lastFailureCode()
                    .startsWith("CONFIGURATION_REJECTED")
                    ? List.of("PUBLISH_NEW_CONFIGURATION")
                    : List.of("RESYNCHRONIZE");
            pollAfter = null;
        } else {
            nextActions = List.of("WAIT");
            pollAfter = RECOMMENDED_POLL_AFTER_MS;
        }
        return new ConfigurationApplicationView(
                application.applicationUid(),
                application.versionNo(),
                application.contentSha256(),
                application.mcuPayloadSha256(),
                application.status(),
                application.version(),
                latest,
                superseded,
                application.reportedVersionNo(),
                application.reportedContentSha256(),
                application.reportedMcuPayloadSha256(),
                application.edgePersistedAt(),
                application.mcuSyncedAt(),
                application.appliedAt(),
                application.lastFailureCode(),
                application.lastFailedAt(),
                task.state(),
                pollAfter,
                nextActions);
    }

    private static ConfigurationDeviceSnapshot configurationDevice(
            ConfigurationVersionRow row) {
        return new ConfigurationDeviceSnapshot(
                row.deviceDisplayName(),
                row.address(),
                row.longitude(),
                row.latitude(),
                row.edgeHeartbeatIntervalMs(),
                row.edgeHeartbeatMissThreshold(),
                row.mcuHeartbeatIntervalMs(),
                row.mcuHeartbeatMissThreshold(),
                row.doorCloseRetryLimit(),
                row.continueDeliveryWaitMs(),
                row.negativeWeightThresholdGram(),
                row.deliveryAutoCloseMs(),
                row.weightMeasurementTimeoutMs(),
                row.deliveryDoorTravelWaitMs(),
                row.cleanSolenoidPulseMs(),
                row.smokeMonitoringEnabled());
    }

    private static String publisherName(ConfigurationVersionRow row) {
        return row.publisherName() == null ? "SYSTEM" : row.publisherName();
    }

    private static boolean locationChanged(
            ConfigurationVersionRow previous,
            ConfigurationDeviceSnapshot next) {
        return !Objects.equals(previous.address(), next.address())
                || !Objects.equals(
                normalizedDecimal(previous.longitude()),
                normalizedDecimal(next.longitude()))
                || !Objects.equals(
                normalizedDecimal(previous.latitude()),
                normalizedDecimal(next.latitude()));
    }

    private Optional<DeviceAssetView> findAssetView(
            String hardwareSn,
            Long tenantId,
            Long organizationId,
            boolean hideUnavailable) {
        StringBuilder sql = new StringBuilder(assetSelect())
                .append(assetFrom())
                .append(" WHERE asset.hardware_sn = ?");
        List<Object> args = new ArrayList<>();
        args.add(hardwareSn);
        appendVisibility(sql, args, tenantId, organizationId, hideUnavailable);
        return jdbc.query(sql.toString(),
                (rs, ignored) -> assetView(rs), args.toArray())
                .stream().findFirst();
    }

    private Optional<DeviceAssetView> findAssetViewByCode(
            String deviceCode,
            Long tenantId,
            Long organizationId,
            boolean hideUnavailable) {
        StringBuilder sql = new StringBuilder(assetSelect())
                .append(assetFrom())
                .append(" WHERE asset.device_public_code = ?");
        List<Object> args = new ArrayList<>();
        args.add(deviceCode);
        appendVisibility(sql, args, tenantId, organizationId, hideUnavailable);
        return jdbc.query(sql.toString(),
                (rs, ignored) -> assetView(rs), args.toArray())
                .stream().findFirst();
    }

    private static void appendVisibility(
            StringBuilder sql,
            List<Object> args,
            Long tenantId,
            Long organizationId,
            boolean hideUnavailable) {
        if (tenantId != null) {
            sql.append(" AND asset.tenant_id = ?");
            args.add(tenantId);
        }
        if (organizationId != null) {
            sql.append(" AND asset.organization_id = ?");
            args.add(organizationId);
        }
        if (hideUnavailable) {
            sql.append(" AND asset.lifecycle_status = 'NORMAL'");
        }
    }

    private static String assetSelect() {
        return """
                SELECT asset.asset_uid, asset.device_public_code,
                       asset.hardware_sn, asset.model_name,
                       asset.production_batch, asset.expected_port_count,
                       tenant.tenant_code, organization.organization_code,
                       asset.acceptance_status, asset.miniapp_qr_status,
                       asset.lifecycle_status, asset.control_version,
                       asset.tenant_assigned_at,
                       asset.organization_assigned_at, asset.accepted_at,
                       asset.disabled_at, asset.retired_at,
                       asset.created_at, asset.updated_at
                """;
    }

    private static String assetFrom() {
        return """
                FROM dev_device_asset asset
                LEFT JOIN iam_tenant tenant ON tenant.id = asset.tenant_id
                LEFT JOIN iam_organization organization
                  ON organization.tenant_id = asset.tenant_id
                 AND organization.id = asset.organization_id
                """;
    }

    private DeviceAssetView assetView(ResultSet rs) throws SQLException {
        String hardwareSn = rs.getString("hardware_sn");
        return new DeviceAssetView(
                UUID.fromString(rs.getString("asset_uid")),
                rs.getString("device_public_code"),
                hardwareSn,
                rs.getString("model_name"),
                rs.getString("production_batch"),
                rs.getInt("expected_port_count"),
                rs.getString("tenant_code"),
                rs.getString("organization_code"),
                rs.getString("acceptance_status"),
                rs.getString("miniapp_qr_status"),
                rs.getString("lifecycle_status"),
                rs.getLong("control_version"),
                instant(rs, "tenant_assigned_at"),
                instant(rs, "organization_assigned_at"),
                instant(rs, "accepted_at"),
                instant(rs, "disabled_at"),
                instant(rs, "retired_at"),
                instant(rs, "created_at"),
                instant(rs, "updated_at"),
                new ComputedOneNetMapping(
                        oneNetProductId,
                        hardwareSn,
                        oneNetProductId != null));
    }

    private Asset asset(String hardwareSn, boolean lock) {
        return jdbc.query("""
                        SELECT id, hardware_sn, device_public_code,
                               model_name, expected_port_count,
                               tenant_id, organization_id,
                               acceptance_status, lifecycle_status,
                               control_version
                        FROM dev_device_asset
                        WHERE hardware_sn = ?
                        """ + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> new Asset(
                        rs.getLong("id"),
                        rs.getString("hardware_sn"),
                        rs.getString("device_public_code"),
                        rs.getString("model_name"),
                        rs.getInt("expected_port_count"),
                        nullableLong(rs, "tenant_id"),
                        nullableLong(rs, "organization_id"),
                        rs.getString("acceptance_status"),
                        rs.getString("lifecycle_status"),
                        rs.getLong("control_version")),
                hardwareSn).stream().findFirst()
                .orElseThrow(TargetDeviceApplication::notFound);
    }

    private List<FactoryBag> factoryBags(long assetId) {
        return jdbc.query("""
                        SELECT port_no, bag_code
                        FROM dev_factory_installed_bag
                        WHERE asset_id = ?
                        ORDER BY port_no
                        FOR UPDATE
                        """,
                (rs, ignored) -> new FactoryBag(
                        rs.getInt("port_no"), rs.getString("bag_code")),
                assetId);
    }

    private DeviceAssetView platformView(String hardwareSn) {
        return findAssetView(hardwareSn, null, null, false)
                .orElseThrow(TargetDeviceApplication::notFound);
    }

    private DeviceAssetView tenantView(String hardwareSn, long tenantId) {
        return findAssetView(hardwareSn, tenantId, null, true)
                .orElseThrow(TargetDeviceApplication::notFound);
    }

    private AcceptanceEvidenceView evidence(ResultSet rs) throws SQLException {
        List<String> reasons = new ArrayList<>();
        JsonNode node = readJson(rs.getString("failure_reasons_json"));
        if (node.isArray()) {
            node.forEach(value -> reasons.add(value.asText()));
        }
        return new AcceptanceEvidenceView(
                UUID.fromString(rs.getString("evidence_uid")),
                rs.getInt("evidence_schema_version"),
                rs.getString("edge_software_version"),
                rs.getString("edge_protocol_version"),
                rs.getBoolean("onenet_online"),
                rs.getBoolean("persistent_store_healthy"),
                rs.getBoolean("trusted_time_healthy"),
                rs.getBoolean("configuration_persistence_healthy"),
                rs.getBoolean("mcu_communication_healthy"),
                rs.getBoolean("sensors_healthy"),
                rs.getBoolean("cameras_capture_healthy"),
                rs.getBoolean("camera_upload_healthy"),
                rs.getBoolean("mcu_simulated"),
                rs.getBoolean("cameras_simulated"),
                rs.getString("evaluation_status"),
                reasons,
                rs.getString("evidence_sha256"),
                instant(rs, "observed_at"),
                instant(rs, "received_at"));
    }

    private Scope authorize(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String capability) {
        AuthorizedDeviceScope authorization = authorizationPort.authorize(
                new DeviceScopeAuthorizationQuery(
                        platformPath,
                        tenantCode,
                        organizationCode,
                        capability));
        Long[] keys = new Long[4];
        authorization.persistenceRef().writeForeignKeysTo(
                (tenantId, organizationId, platformAdminId, staffAccountId) -> {
                    keys[0] = tenantId;
                    keys[1] = organizationId;
                    keys[2] = platformAdminId;
                    keys[3] = staffAccountId;
                });
        return new Scope(
                authorization.platformActor(),
                authorization.principalUid(),
                authorization.sessionUid(),
                authorization.actorDisplayName(),
                authorization.tenantEnabled(),
                authorization.organizationEnabled(),
                keys[0], keys[1], keys[2], keys[3]);
    }

    private <T> T command(
            UUID operationUid,
            Scope scope,
            String actionCode,
            String targetType,
            String targetStableKey,
            Object request,
            Class<T> responseType,
            Supplier<CommandResult<T>> work) {
        requireUuidV4(operationUid);
        TargetWebAuditRequestContext.describe(actionCode, targetStableKey);
        String fingerprint = Integer.toHexString(Objects.hash(
                scope.principalUid(), actionCode, targetStableKey,
                writeJson(request)));
        Optional<SuccessfulAudit> previous =
                auditPort.findSuccessful(operationUid);
        if (previous.isPresent()) {
            SuccessfulAudit audit = previous.get();
            JsonNode summary = readJson(audit.safeChangeSummaryJson());
            boolean sameActor = scope.platformActor()
                    ? audit.actorKind() == AuditActorKind.PLATFORM_ADMIN
                    && Objects.equals(audit.platformAdminId(), scope.platformAdminId())
                    : audit.actorKind() == AuditActorKind.STAFF_ACCOUNT
                    && Objects.equals(audit.staffAccountId(), scope.staffAccountId());
            if (!sameActor
                    || !actionCode.equals(audit.actionCode())
                    || !targetType.equals(audit.targetType())
                    || !targetStableKey.equals(audit.targetStableKey())
                    || !fingerprint.equals(summary.path("fingerprint").asText())) {
                throw idempotencyConflict();
            }
            try {
                return objectMapper.treeToValue(
                        summary.path("response"), responseType);
            } catch (Exception exception) {
                throw idempotencyConflict();
            }
        }
        CommandResult<T> result = work.get();
        Map<String, Object> summary = new LinkedHashMap<>();
        summary.put("fingerprint", fingerprint);
        summary.put("before", result.before());
        summary.put("after", result.after());
        summary.put("response", result.response());
        auditPort.append(new AuditEntry(
                UUID.randomUUID(), UUID.randomUUID(), operationUid,
                scope.organizationId() != null
                        ? AuditScopeKind.ORGANIZATION
                        : scope.tenantId() != null
                        ? AuditScopeKind.TENANT : AuditScopeKind.PLATFORM,
                scope.tenantId(), scope.organizationId(),
                scope.platformActor()
                        ? AuditActorKind.PLATFORM_ADMIN
                        : AuditActorKind.STAFF_ACCOUNT,
                scope.platformAdminId(), scope.staffAccountId(),
                null, null, scope.actorDisplayName(), actionCode,
                targetType, targetStableKey, "WEB", "SUCCEEDED",
                scope.sessionUid(), result.reason(), writeJson(summary),
                Instant.now()));
        return result.response();
    }

    private Map<String, Object> configurationCommandEnvelope(
            UUID commandUid,
            String hardwareSn,
            UUID applicationUid,
            Instant issuedAt,
            Map<String, Object> payload,
            byte[] payloadSha256) {
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("schemaVersion", 2);
        envelope.put("commandUid", commandUid.toString());
        envelope.put("commandType", "APPLY_CONFIGURATION");
        envelope.put("targetDeviceName", hardwareSn);
        envelope.put("target", Map.of(
                "type", CONFIGURATION_TARGET_TYPE,
                "uid", applicationUid.toString()));
        envelope.put("issuedAt", issuedAt.toString());
        envelope.put(
                "expiresAt",
                issuedAt.plusSeconds(
                        CONFIGURATION_COMMAND_VALIDITY_SECONDS).toString());
        envelope.put("payloadSchemaVersion", 2);
        envelope.put("payloadSha256", canonicalizer.hex(payloadSha256));
        envelope.put("payload", payload);
        envelope.put("cosGrant", null);
        return envelope;
    }

    private Map<String, Object> configurationTaskSnapshot(
            UUID commandUid,
            String hardwareSn,
            UUID applicationUid,
            byte[] envelopeSha256) {
        Map<String, Object> snapshot = new LinkedHashMap<>();
        snapshot.put("schemaVersion", 2);
        snapshot.put("commandUid", commandUid.toString());
        snapshot.put("commandType", "APPLY_CONFIGURATION");
        snapshot.put("targetDeviceName", hardwareSn);
        snapshot.put("target", Map.of(
                "type", CONFIGURATION_TARGET_TYPE,
                "uid", applicationUid.toString()));
        snapshot.put("payloadSchemaVersion", 2);
        snapshot.put(
                "semanticPayloadSha256",
                canonicalizer.hex(envelopeSha256));
        return snapshot;
    }

    private static String configurationApplicationCollectionUrl(
            String organizationCode, String deviceCode) {
        return "/api/v1/web/organizations/" + organizationCode
                + "/devices/" + deviceCode
                + "/configuration-applications";
    }

    private NormalizedAssetCreate normalizeCreate(
            CreateDeviceAssetRequest request) {
        if (request == null || request.expectedPortCount() == null
                || request.factoryBags() == null) {
            throw invalid("设备资产请求不完整");
        }
        int portCount = request.expectedPortCount();
        if (portCount < 1 || portCount > 6
                || request.factoryBags().size() != portCount) {
            throw invalid("厂家初始袋必须恰好覆盖每个投口");
        }
        Set<Integer> ports = new LinkedHashSet<>();
        Set<String> codes = new LinkedHashSet<>();
        List<FactoryBag> bags = new ArrayList<>();
        for (FactoryInstalledBagRequest bag : request.factoryBags()) {
            if (bag == null || bag.portNo() == null
                    || bag.portNo() < 1 || bag.portNo() > portCount) {
                throw invalid("厂家初始袋投口编号无效");
            }
            String code = required(bag.bagCode(), 64, "bagCode");
            if (!code.matches("[A-Za-z0-9_-]{8,64}")
                    || !ports.add(bag.portNo()) || !codes.add(code)) {
                throw invalid("厂家初始袋编号重复或格式无效");
            }
            bags.add(new FactoryBag(bag.portNo(), code));
        }
        bags.sort(java.util.Comparator.comparingInt(FactoryBag::portNo));
        return new NormalizedAssetCreate(
                normalizeHardwareSn(request.hardwareSn()),
                required(request.modelCode(), 100, "modelCode"),
                optional(request.productionBatch(), 64),
                portCount,
                List.copyOf(bags));
    }

    private long insertAndReturnKey(String sql, Object... args) {
        org.springframework.jdbc.support.GeneratedKeyHolder holder =
                new org.springframework.jdbc.support.GeneratedKeyHolder();
        jdbc.update(connection -> {
            var statement = connection.prepareStatement(
                    sql, java.sql.Statement.RETURN_GENERATED_KEYS);
            for (int index = 0; index < args.length; index++) {
                statement.setObject(index + 1, args[index]);
            }
            return statement;
        }, holder);
        Number key = holder.getKey();
        if (key == null) {
            throw new IllegalStateException("generated device key is missing");
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
        try {
            return objectMapper.writeValueAsString(value);
        } catch (Exception exception) {
            throw new IllegalStateException("device JSON cannot be encoded", exception);
        }
    }

    private JsonNode readJson(String value) {
        try {
            return objectMapper.readTree(value);
        } catch (Exception exception) {
            throw new IllegalStateException("device JSON cannot be decoded", exception);
        }
    }

    private static CommandResult<DeviceAssetView> changed(
            Asset before,
            DeviceAssetView response,
            String reason) {
        return new CommandResult<>(
                response,
                Map.of(
                        "lifecycleStatus", before.lifecycleStatus(),
                        "version", before.controlVersion()),
                snapshot(response),
                reason);
    }

    private static CommandResult<DeviceAssetView> unchanged(
            DeviceAssetView response) {
        Map<String, Object> state = snapshot(response);
        return new CommandResult<>(response, state, state, null);
    }

    private static Map<String, Object> snapshot(DeviceAssetView view) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("assetUid", view.assetUid());
        result.put("hardwareSn", view.hardwareSn());
        result.put("tenantCode", view.tenantCode());
        result.put("organizationCode", view.organizationCode());
        result.put("acceptanceStatus", view.acceptanceStatus());
        result.put("lifecycleStatus", view.lifecycleStatus());
        result.put("version", view.version());
        return result;
    }

    private static String randomPublicCode() {
        byte[] random = new byte[24];
        PUBLIC_CODE_RANDOM.nextBytes(random);
        return "Dv_" + Base64.getUrlEncoder()
                .withoutPadding().encodeToString(random);
    }

    private static BigDecimal nullableDecimal(String value) {
        return value == null ? null : new BigDecimal(value);
    }

    private static String decimalString(
            BigDecimal value, int maximumScale) {
        if (value == null) {
            return null;
        }
        BigDecimal normalized = value.stripTrailingZeros();
        if (normalized.scale() < 0) {
            normalized = normalized.setScale(0);
        }
        if (normalized.scale() > maximumScale) {
            normalized = normalized.setScale(
                    maximumScale, java.math.RoundingMode.UNNECESSARY);
        }
        return normalized.toPlainString();
    }

    private static String normalizedDecimal(String value) {
        return value == null
                ? null
                : new BigDecimal(value).stripTrailingZeros().toPlainString();
    }

    private static String normalizeHardwareSn(String value) {
        String result = required(value, 64, "hardwareSn");
        if (!result.matches("[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")) {
            throw invalid("硬件序列号格式无效");
        }
        return result;
    }

    private static String normalizeDeviceCode(String value) {
        String result = required(value, 64, "deviceCode");
        if (!result.matches("Dv_[A-Za-z0-9_-]{24,61}")) {
            throw notFound();
        }
        return result;
    }

    private static String required(
            String value, int maximumLength, String field) {
        if (value == null || value.isBlank()) {
            throw invalid(field + "不能为空");
        }
        String result = value.trim();
        if (result.length() > maximumLength) {
            throw invalid(field + "过长");
        }
        return result;
    }

    private static String optional(String value, int maximumLength) {
        return value == null || value.isBlank()
                ? null : required(value, maximumLength, "value");
    }

    private static String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value.trim();
    }

    private static String escapeLike(String value) {
        return value.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_");
    }

    private static Long nullableLong(ResultSet rs, String column)
            throws SQLException {
        long value = rs.getLong(column);
        return rs.wasNull() ? null : value;
    }

    private static Instant instant(ResultSet rs, String column)
            throws SQLException {
        LocalDateTime value = rs.getObject(column, LocalDateTime.class);
        return value == null ? null : value.toInstant(ZoneOffset.UTC);
    }

    private static void requireVersion(long actual, long expected) {
        if (actual != expected) {
            throw new TargetApiException(
                    409,
                    "COMMON.VERSION_CONFLICT",
                    "设备状态已变化，请刷新后重试",
                    true,
                    Map.of("currentVersion", actual));
        }
    }

    private static void requireSingle(int updated) {
        if (updated != 1) {
            throw conflict(
                    "DEVICE.CONCURRENT_CHANGE",
                    "设备状态已被其他请求修改，请刷新后重试");
        }
    }

    private static void requireEnabledTenant(Scope scope) {
        if (!scope.tenantEnabled()) {
            throw unprocessable("TENANT.DISABLED", "当前租户已禁用");
        }
    }

    private static void requireEnabledScope(Scope scope) {
        requireEnabledTenant(scope);
        if (!scope.organizationEnabled()) {
            throw unprocessable(
                    "ORGANIZATION.DISABLED", "当前机构已禁用");
        }
    }

    private static void requireUuidV4(UUID value) {
        if (value == null || value.version() != 4 || value.variant() != 2) {
            throw invalid("Idempotency-Key 必须是 UUIDv4");
        }
    }

    private static TargetApiException invalid(String message) {
        return new TargetApiException(
                400, "COMMON.INVALID_REQUEST", message, false, Map.of());
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404, "COMMON.RESOURCE_NOT_FOUND", "设备不存在或不可用");
    }

    private static TargetApiException conflict(String code, String message) {
        return new TargetApiException(409, code, message);
    }

    private static TargetApiException unprocessable(
            String code, String message) {
        return new TargetApiException(422, code, message);
    }

    private static TargetApiException idempotencyConflict() {
        return conflict(
                "COMMON.IDEMPOTENCY_CONFLICT",
                "该 Idempotency-Key 已用于其他设备操作");
    }

    private static TargetApiException versionConflict(long current) {
        return new TargetApiException(
                409,
                "COMMON.VERSION_CONFLICT",
                "配置版本已变化，请刷新后重试",
                true,
                Map.of("currentVersion", current));
    }

    private static TargetApiException applicationNotFound() {
        return new TargetApiException(
                404,
                "DEVICE.CONFIGURATION_APPLICATION_NOT_FOUND",
                "配置应用记录不存在或不可用");
    }

    private static IllegalStateException invariant() {
        return new IllegalStateException("device configuration invariant failed");
    }

    private record Scope(
            boolean platformActor,
            UUID principalUid,
            UUID sessionUid,
            String actorDisplayName,
            boolean tenantEnabled,
            boolean organizationEnabled,
            Long tenantId,
            Long organizationId,
            Long platformAdminId,
            Long staffAccountId) {
    }

    private record Asset(
            long id,
            String hardwareSn,
            String devicePublicCode,
            String modelCode,
            int expectedPortCount,
            Long tenantId,
            Long organizationId,
            String acceptanceStatus,
            String lifecycleStatus,
            long controlVersion) {
    }

    private record Tenant(long id, String code, String status) {
    }

    private record Organization(long id, String code, String status) {
    }

    private record FactoryBag(int portNo, String bagCode) {
    }

    private record NormalizedAssetCreate(
            String hardwareSn,
            String modelCode,
            String productionBatch,
            int expectedPortCount,
            List<FactoryBag> factoryBags) {
    }

    private record EvidenceDecision(
            String status,
            byte[] sha256,
            String failureReasonsJson,
            LocalDateTime receivedAt) {
    }

    private record ConfigurationVersionRow(
            long id,
            long versionNo,
            int schemaVersion,
            String deviceDisplayName,
            String address,
            String longitude,
            String latitude,
            long edgeHeartbeatIntervalMs,
            long edgeHeartbeatMissThreshold,
            long mcuHeartbeatIntervalMs,
            long mcuHeartbeatMissThreshold,
            long doorCloseRetryLimit,
            long continueDeliveryWaitMs,
            long negativeWeightThresholdGram,
            long deliveryAutoCloseMs,
            long weightMeasurementTimeoutMs,
            long deliveryDoorTravelWaitMs,
            long cleanSolenoidPulseMs,
            boolean smokeMonitoringEnabled,
            String contentSha256,
            String mcuPayloadSha256,
            String publicationSource,
            String publisherName,
            Instant publishedAt,
            UUID applicationUid,
            String applicationStatus,
            long applicationVersion) {
    }

    private record ApplicationRow(
            long id,
            UUID applicationUid,
            long versionNo,
            long latestVersionNo,
            String contentSha256,
            String mcuPayloadSha256,
            String status,
            long version,
            Long reportedVersionNo,
            String reportedContentSha256,
            String reportedMcuPayloadSha256,
            Instant edgePersistedAt,
            Instant mcuSyncedAt,
            Instant appliedAt,
            String lastFailureCode,
            Instant lastFailedAt) {
    }

    private record CommandResult<T>(
            T response,
            Map<String, Object> before,
            Map<String, Object> after,
            String reason) {
    }
}
