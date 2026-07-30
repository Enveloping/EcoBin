package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.port.DevicePortBusinessSnapshotPort;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ActivateDeploymentRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ComputedOneNetMapping;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationAcceptedView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationApplicationSummary;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationApplicationView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationDeviceSnapshot;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationPortSnapshot;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationReleaseRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationVersionSummary;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationVersionView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.CreateDeploymentRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.CreateDeviceAssetRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.CurrentDeploymentSummary;
import org.enveloping.ecobin.device.web.v1.DeviceModels.CursorPage;
import org.enveloping.ecobin.device.web.v1.DeviceModels.DeploymentAssetSummary;
import org.enveloping.ecobin.device.web.v1.DeviceModels.DeploymentRuntimeView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.DeploymentVersionCommand;
import org.enveloping.ecobin.device.web.v1.DeviceModels.DeploymentView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.DeviceAssetView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.PageData;
import org.enveloping.ecobin.device.web.v1.DeviceModels.PortBusinessSummary;
import org.enveloping.ecobin.device.web.v1.DeviceModels.PortRuntimeView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.PortView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.RuntimeConfigurationSummary;
import org.enveloping.ecobin.device.web.v1.DeviceModels.RuntimeHealthSummary;
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
import org.enveloping.ecobin.identity.api.port.DeviceScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.query.DeviceScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedDeviceScope;
import org.enveloping.ecobin.framework.web.TargetWebAuditRequestContext;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.PreparedStatementCreator;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.jdbc.support.KeyHolder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
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

@Service
public class TargetDeviceApplication {

    private static final int DEFAULT_PAGE_SIZE = 20;
    private static final int MAX_PAGE_SIZE = 200;
    private static final int DEFAULT_CURSOR_LIMIT = 20;
    private static final int MAX_CURSOR_LIMIT = 100;
    private static final long RECOMMENDED_POLL_AFTER_MS = 3_000;
    /*
     * Configuration is not a 60-second physical-start authorization. Keep the
     * stable command reusable for operational resynchronization while still
     * carrying the mandatory machine-contract deadline.
     */
    private static final long CONFIGURATION_COMMAND_VALIDITY_SECONDS =
            10L * 365 * 24 * 60 * 60;
    private static final String CONFIGURATION_TASK_TYPE =
            "ENSURE_DEVICE_CONFIGURATION";
    private static final String CONFIGURATION_TARGET_TYPE =
            "CONFIGURATION_APPLICATION";

    private final JdbcTemplate jdbc;
    private final DeviceScopeAuthorizationPort authorizationPort;
    private final DeviceConfigurationCanonicalizer canonicalizer;
    private final InitialDeviceConfigurationFactory
            initialConfigurationFactory;
    private final AuditPort auditPort;
    private final ReliableDeviceTaskRegistrationPort taskRegistrationPort;
    private final ReliableDeviceTaskStatusPort taskStatusPort;
    private final ReliableTaskWakePort taskWakePort;
    private final DeviceCommandTaskRefFactory taskRefFactory;
    private final DevicePortBusinessSnapshotPort portBusinessPort;
    private final ObjectMapper objectMapper;
    private final String oneNetProductId;

    public TargetDeviceApplication(
            JdbcTemplate jdbc,
            DeviceScopeAuthorizationPort authorizationPort,
            DeviceConfigurationCanonicalizer canonicalizer,
            InitialDeviceConfigurationFactory initialConfigurationFactory,
            AuditPort auditPort,
            ReliableDeviceTaskRegistrationPort taskRegistrationPort,
            ReliableDeviceTaskStatusPort taskStatusPort,
            ReliableTaskWakePort taskWakePort,
            DeviceCommandTaskRefFactory taskRefFactory,
            DevicePortBusinessSnapshotPort portBusinessPort,
            ObjectMapper objectMapper,
            @Value("${onenet.product-id:}") String oneNetProductId) {
        this.jdbc = jdbc;
        this.authorizationPort = authorizationPort;
        this.canonicalizer = canonicalizer;
        this.initialConfigurationFactory = initialConfigurationFactory;
        this.auditPort = auditPort;
        this.taskRegistrationPort = taskRegistrationPort;
        this.taskStatusPort = taskStatusPort;
        this.taskWakePort = taskWakePort;
        this.taskRefFactory = taskRefFactory;
        this.portBusinessPort = portBusinessPort;
        this.objectMapper = objectMapper;
        this.oneNetProductId = blankToNull(oneNetProductId);
    }

    @Transactional(readOnly = true)
    public PageData<DeviceAssetView> listAssets(
            int requestedPage,
            int requestedPageSize,
            String hardwareSn,
            String modelCode,
            String productionBatch,
            String lifecycleStatus) {
        authorize(true, null, null, "device.read");
        int page = page(requestedPage);
        int pageSize = pageSize(requestedPageSize);
        StringBuilder predicate = new StringBuilder("""
                FROM dev_device_asset a
                LEFT JOIN dev_asset_active_deployment active
                  ON active.asset_id = a.id
                LEFT JOIN dev_device_deployment d
                  ON d.id = active.deployment_id
                LEFT JOIN iam_tenant t ON t.id = active.tenant_id
                LEFT JOIN iam_organization o
                  ON o.tenant_id = active.tenant_id
                 AND o.id = active.organization_id
                WHERE 1 = 1
                """);
        List<Object> parameters = new ArrayList<>();
        addContains(predicate, parameters, "a.hardware_sn", hardwareSn);
        addContains(predicate, parameters, "a.model_name", modelCode);
        addContains(
                predicate, parameters, "a.production_batch", productionBatch);
        String status = upperOrNull(lifecycleStatus);
        if (status != null) {
            predicate.append(" AND a.lifecycle_status = ?");
            parameters.add(status);
        }
        long total = jdbc.queryForObject(
                "SELECT COUNT(*) " + predicate,
                Long.class,
                parameters.toArray());
        List<Object> listParameters = new ArrayList<>(parameters);
        listParameters.add(pageSize);
        listParameters.add((long) (page - 1) * pageSize);
        List<DeviceAssetView> items = jdbc.query("""
                        SELECT a.id, a.hardware_sn, a.model_name,
                               a.production_batch, a.expected_port_count,
                               a.lifecycle_status, a.lock_version,
                               a.created_at, a.updated_at,
                               d.public_code AS deployment_code,
                               d.lifecycle_status AS deployment_status,
                               d.business_enabled,
                               t.tenant_code, o.organization_code
                        """
                        + predicate
                        + " ORDER BY a.id DESC LIMIT ? OFFSET ?",
                (rs, ignored) -> assetView(rs),
                listParameters.toArray());
        return new PageData<>(items, page, pageSize, total);
    }

    @Transactional(readOnly = true)
    public DeviceAssetView asset(String hardwareSn) {
        authorize(true, null, null, "device.read");
        return findAssetView(normalizeHardwareSn(hardwareSn))
                .orElseThrow(TargetDeviceApplication::assetNotFound);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public DeviceAssetView createAsset(
            UUID operationUid,
            CreateDeviceAssetRequest request) {
        AuthorizedScope scope = authorize(
                true, null, null, "device.manage");
        if (request == null) {
            throw invalidRequest();
        }
        String hardwareSn = normalizeHardwareSn(request.hardwareSn());
        String modelCode = requiredTrimmed(
                request.modelCode(), 100, "modelCode");
        String productionBatch = optionalTrimmed(
                request.productionBatch(), 64, "productionBatch");
        if (request.expectedPortCount() == null
                || request.expectedPortCount() < 1
                || request.expectedPortCount() > 6) {
            throw invalidRequest();
        }
        CreateDeviceAssetRequest normalized =
                new CreateDeviceAssetRequest(
                        hardwareSn,
                        modelCode,
                        productionBatch,
                        request.expectedPortCount());
        return command(
                operationUid,
                scope,
                "device.asset.create",
                "DEVICE_ASSET",
                hardwareSn,
                normalized,
                DeviceAssetView.class,
                () -> {
                    if (findAssetView(hardwareSn).isPresent()) {
                        throw new TargetApiException(
                                409,
                                "DEVICE.ASSET_ALREADY_EXISTS",
                                "硬件序列号已经登记");
                    }
                    LocalDateTime now = databaseNow();
                    try {
                        jdbc.update("""
                                        INSERT INTO dev_device_asset (
                                            hardware_sn, model_name,
                                            production_batch,
                                            expected_port_count,
                                            lifecycle_status,
                                            retired_at, retirement_reason,
                                            lock_version, created_at, updated_at
                                        ) VALUES (
                                            ?, ?, ?, ?, 'IN_STOCK',
                                            NULL, NULL, 0, ?, ?
                                        )
                                        """,
                                hardwareSn,
                                modelCode,
                                productionBatch,
                                request.expectedPortCount(),
                                now,
                                now);
                    } catch (DataIntegrityViolationException exception) {
                        throw new TargetApiException(
                                409,
                                "DEVICE.ASSET_ALREADY_EXISTS",
                                "硬件序列号已经登记");
                    }
                    DeviceAssetView response = findAssetView(hardwareSn)
                            .orElseThrow(TargetDeviceApplication::invariant);
                    return new CommandResult<>(
                            response,
                            Map.of(),
                            assetAuditSnapshot(response),
                            null);
                });
    }

    @Transactional(readOnly = true)
    public PageData<DeploymentView> listDeployments(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            int requestedPage,
            int requestedPageSize,
            String lifecycleStatus,
            Boolean businessEnabled,
            String hardwareSn,
            String edgeConnectionStatus,
            String configurationApplicationStatus) {
        AuthorizedScope scope = authorize(
                platformPath,
                platformPath ? tenantCode : null,
                organizationCode,
                "device.read");
        int page = page(requestedPage);
        int pageSize = pageSize(requestedPageSize);
        StringBuilder predicate = new StringBuilder("""
                WHERE d.tenant_id = ?
                  AND d.organization_id = ?
                """);
        List<Object> parameters = new ArrayList<>();
        parameters.add(scope.tenantId());
        parameters.add(scope.organizationId());
        String status = upperOrNull(lifecycleStatus);
        if (status != null) {
            predicate.append(" AND d.lifecycle_status = ?");
            parameters.add(status);
        }
        if (businessEnabled != null) {
            predicate.append(" AND d.business_enabled = ?");
            parameters.add(businessEnabled);
        }
        addContains(predicate, parameters, "a.hardware_sn", hardwareSn);
        String edge = upperOrNull(edgeConnectionStatus);
        if (edge != null) {
            predicate.append(
                    " AND runtime.edge_connection_status = ?");
            parameters.add(edge);
        }
        String appStatus = upperOrNull(configurationApplicationStatus);
        if (appStatus != null) {
            predicate.append(" AND app.status = ?");
            parameters.add(appStatus);
        }
        String joins = deploymentJoins();
        long total = jdbc.queryForObject(
                "SELECT COUNT(*) FROM dev_device_deployment d "
                        + joins + predicate,
                Long.class,
                parameters.toArray());
        List<Object> listParameters = new ArrayList<>(parameters);
        listParameters.add(pageSize);
        listParameters.add((long) (page - 1) * pageSize);
        List<DeploymentView> items = jdbc.query(
                deploymentSelect()
                        + " FROM dev_device_deployment d "
                        + joins
                        + predicate
                        + " ORDER BY d.created_at DESC, d.id DESC"
                        + " LIMIT ? OFFSET ?",
                (rs, ignored) -> deploymentRow(rs).view(),
                listParameters.toArray());
        return new PageData<>(items, page, pageSize, total);
    }

    @Transactional(readOnly = true)
    public DeploymentView deployment(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String deploymentCode) {
        AuthorizedScope scope = authorize(
                platformPath,
                platformPath ? tenantCode : null,
                organizationCode,
                "device.read");
        return findDeployment(
                scope,
                normalizeDeploymentCode(deploymentCode),
                false).view();
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public DeploymentView createDeployment(
            UUID operationUid,
            String tenantCode,
            String organizationCode,
            CreateDeploymentRequest request) {
        AuthorizedScope scope = authorize(
                true,
                tenantCode,
                organizationCode,
                "device.manage");
        requireEnabledScope(scope);
        if (request == null || request.expectedAssetVersion() == null) {
            throw invalidRequest();
        }
        String hardwareSn = normalizeHardwareSn(request.hardwareSn());
        CreateDeploymentRequest normalized = new CreateDeploymentRequest(
                hardwareSn, request.expectedAssetVersion());
        String target = scope.tenantCode() + "|"
                + scope.organizationCode() + "|asset:" + hardwareSn;
        return command(
                operationUid,
                scope,
                "device.deployment.create",
                "DEVICE_DEPLOYMENT",
                target,
                normalized,
                DeploymentView.class,
                () -> createDeployment(
                        operationUid, scope, normalized));
    }

    @Transactional(readOnly = true)
    public List<PortView> ports(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String deploymentCode) {
        AuthorizedScope scope = authorize(
                platformPath,
                platformPath ? tenantCode : null,
                organizationCode,
                "device.read");
        DeploymentRow deployment = findDeployment(
                scope, normalizeDeploymentCode(deploymentCode), false);
        return jdbc.query("""
                        SELECT p.port_no,
                               snapshot.display_name,
                               snapshot.business_enabled,
                               snapshot.unit_price_yuan_per_kg,
                               snapshot.fullness_mode,
                               config.version_no
                        FROM dev_port p
                        LEFT JOIN dev_config_version config
                          ON config.id = (
                              SELECT latest.id
                              FROM dev_config_version latest
                              WHERE latest.tenant_id = p.tenant_id
                                AND latest.organization_id =
                                    p.organization_id
                                AND latest.deployment_id = p.deployment_id
                              ORDER BY latest.version_no DESC
                              LIMIT 1
                          )
                        LEFT JOIN dev_port_config_snapshot snapshot
                          ON snapshot.tenant_id = p.tenant_id
                         AND snapshot.organization_id = p.organization_id
                         AND snapshot.deployment_id = p.deployment_id
                         AND snapshot.config_version_id = config.id
                         AND snapshot.port_id = p.id
                        WHERE p.tenant_id = ?
                          AND p.organization_id = ?
                          AND p.deployment_id = ?
                        ORDER BY p.port_no
                        """,
                (rs, ignored) -> new PortView(
                        rs.getInt("port_no"),
                        rs.getString("display_name"),
                        nullableBoolean(rs, "business_enabled"),
                        decimalString(
                                rs.getBigDecimal(
                                        "unit_price_yuan_per_kg"),
                                4),
                        rs.getString("fullness_mode"),
                        nullableLong(rs, "version_no")),
                scope.tenantId(),
                scope.organizationId(),
                deployment.id());
    }

    private CommandResult<DeploymentView> createDeployment(
            UUID operationUid,
            AuthorizedScope scope,
            CreateDeploymentRequest request) {
        AssetRow asset = lockAsset(request.hardwareSn());
        if (!"IN_STOCK".equals(asset.lifecycleStatus())) {
            throw new TargetApiException(
                    409,
                    "DEVICE.ASSET_NOT_IN_STOCK",
                    "设备资产不处于库存状态");
        }
        if (asset.version() != request.expectedAssetVersion()) {
            throw versionConflict(asset.version());
        }
        Integer activeCount = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_asset_active_deployment
                        WHERE asset_id = ?
                        """,
                Integer.class,
                asset.id());
        if (activeCount != null && activeCount > 0) {
            throw new TargetApiException(
                    409,
                    "DEVICE.ASSET_ALREADY_DEPLOYED",
                    "设备资产已经存在当前部署");
        }

        LocalDateTime now = databaseNow();
        String deploymentCode = "Dp_"
                + UUID.randomUUID().toString().replace("-", "");
        long deploymentId = insertAndReturnKey("""
                INSERT INTO dev_device_deployment (
                    tenant_id, organization_id, asset_id, public_code,
                    lifecycle_status, business_enabled, commissioned_at,
                    enabled_at, ended_at, end_method, end_reason,
                    lock_version, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, 'COMMISSIONING', 0, ?,
                    NULL, NULL, NULL, NULL, 0, ?, ?
                )
                """,
                scope.tenantId(),
                scope.organizationId(),
                asset.id(),
                deploymentCode,
                now,
                now,
                now);
        jdbc.update("""
                        INSERT INTO dev_asset_active_deployment (
                            asset_id, tenant_id, organization_id,
                            deployment_id, acquired_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                asset.id(),
                scope.tenantId(),
                scope.organizationId(),
                deploymentId,
                now);
        jdbc.update("""
                        INSERT INTO dev_deployment_runtime_state (
                            deployment_id, tenant_id, organization_id,
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
                            ?, ?, ?,
                            'UNKNOWN', 'UNKNOWN',
                            'SAFETY_BLOCKED', 'UNKNOWN',
                            'UNKNOWN', 'UNKNOWN',
                            'UNKNOWN', NULL,
                            NULL, NULL,
                            NULL, NULL, NULL,
                            NULL, NULL,
                            NULL,
                            NULL, NULL, NULL,
                            NULL, NULL, NULL,
                            NULL, NULL,
                            0, ?, ?
                        )
                        """,
                deploymentId,
                scope.tenantId(),
                scope.organizationId(),
                now,
                now);
        for (int portNo = 1;
             portNo <= asset.expectedPortCount();
             portNo++) {
            long portId = insertAndReturnKey("""
                    INSERT INTO dev_port (
                        tenant_id, organization_id, deployment_id,
                        port_no, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    scope.tenantId(),
                    scope.organizationId(),
                    deploymentId,
                    portNo,
                    now);
            jdbc.update("""
                            INSERT INTO dev_port_runtime_state (
                                port_id, tenant_id, organization_id,
                                deployment_id, delivery_door_state,
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
                                ?, ?, ?, ?,
                                'UNKNOWN', 'UNKNOWN', 'UNKNOWN',
                                'UNKNOWN', 'UNKNOWN', 'UNKNOWN',
                                'INFERRED_FROM_LOCK_POWER',
                                'UNKNOWN', 'UNKNOWN', 'UNKNOWN',
                                'UNKNOWN', 'UNKNOWN', 'SAFETY_BLOCKED',
                                NULL, NULL, 0, ?, ?
                            )
                            """,
                    portId,
                    scope.tenantId(),
                    scope.organizationId(),
                    deploymentId,
                    now,
                    now);
        }
        byte[] faultKey = sha256(
                ("INITIAL_COMMISSIONING|" + deploymentCode)
                        .getBytes(StandardCharsets.UTF_8));
        jdbc.update("""
                        INSERT INTO dev_device_fault_event (
                            fault_uid, tenant_id, organization_id,
                            deployment_id, port_id, component_type,
                            fault_code, fault_key, impact_level, status,
                            first_source_kind, first_source_edge_event_id,
                            first_source_edge_event_type,
                            first_source_evidence_sha256,
                            first_detected_at, last_detected_at,
                            discovery_count, recovery_source_kind,
                            recovery_source_edge_event_id,
                            recovery_source_edge_event_type,
                            recovery_method, recovery_audit_log_id,
                            recovered_at, recovered_by_staff_account_id,
                            recovery_reason, lock_version, created_at
                        ) VALUES (
                            ?, ?, ?, ?, NULL, 'DEVICE',
                            'INITIAL_COMMISSIONING', ?,
                            'BUSINESS_BLOCKING', 'OPEN',
                            'INTERNAL_DETECTION', NULL, NULL, ?,
                            ?, ?, 1, NULL, NULL, NULL,
                            NULL, NULL, NULL, NULL, NULL, 0, ?
                        )
                        """,
                UUID.randomUUID().toString(),
                scope.tenantId(),
                scope.organizationId(),
                deploymentId,
                faultKey,
                faultKey,
                now,
                now,
                now);
        int updated = jdbc.update("""
                        UPDATE dev_device_asset
                        SET lifecycle_status = 'IN_USE',
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND lifecycle_status = 'IN_STOCK'
                          AND lock_version = ?
                        """,
                now,
                asset.id(),
                asset.version());
        requireSingleRow(updated, "advance deployed asset");
        ConfigurationReleaseRequest initialConfiguration =
                initialConfigurationFactory.create(
                        asset.hardwareSn(),
                        asset.modelCode(),
                        asset.expectedPortCount());
        releaseConfiguration(
                operationUid,
                scope,
                deploymentCode,
                applicationCollectionUrl(
                        true,
                        scope.tenantCode(),
                        scope.organizationCode(),
                        deploymentCode),
                initialConfiguration);
        DeploymentView response = findDeployment(
                scope, deploymentCode, false).view();
        return new CommandResult<>(
                response,
                Map.of(
                        "assetLifecycleStatus", "IN_STOCK",
                        "assetVersion", asset.version()),
                deploymentAuditSnapshot(response),
                null);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public DeploymentView activate(
            UUID operationUid,
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String deploymentCode,
            ActivateDeploymentRequest request) {
        AuthorizedScope scope = authorize(
                platformPath,
                platformPath ? tenantCode : null,
                organizationCode,
                "device.manage");
        if (request == null
                || request.expectedVersion() == null
                || request.expectedConfigurationVersion() == null
                || request.acceptanceConfirmed() == null) {
            throw invalidRequest();
        }
        String code = normalizeDeploymentCode(deploymentCode);
        return command(
                operationUid,
                scope,
                "device.deployment.activate",
                "DEVICE_DEPLOYMENT",
                code,
                request,
                DeploymentView.class,
                () -> mutateDeployment(
                        scope,
                        code,
                        request.expectedVersion(),
                        request.reason(),
                        DeploymentMutation.ACTIVATE,
                        request.expectedConfigurationVersion(),
                        request.acceptanceConfirmed()));
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public DeploymentView deactivate(
            UUID operationUid,
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String deploymentCode,
            DeploymentVersionCommand request) {
        return deploymentVersionCommand(
                operationUid,
                platformPath,
                tenantCode,
                organizationCode,
                deploymentCode,
                request,
                DeploymentMutation.DEACTIVATE);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public DeploymentView enableBusiness(
            UUID operationUid,
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String deploymentCode,
            DeploymentVersionCommand request) {
        return deploymentVersionCommand(
                operationUid,
                platformPath,
                tenantCode,
                organizationCode,
                deploymentCode,
                request,
                DeploymentMutation.ENABLE_BUSINESS);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public DeploymentView disableBusiness(
            UUID operationUid,
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String deploymentCode,
            DeploymentVersionCommand request) {
        return deploymentVersionCommand(
                operationUid,
                platformPath,
                tenantCode,
                organizationCode,
                deploymentCode,
                request,
                DeploymentMutation.DISABLE_BUSINESS);
    }

    @Transactional(readOnly = true)
    public DeploymentRuntimeView runtime(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String deploymentCode) {
        AuthorizedScope scope = authorize(
                platformPath,
                platformPath ? tenantCode : null,
                organizationCode,
                "device.read");
        DeploymentRow deployment = findDeployment(
                scope, normalizeDeploymentCode(deploymentCode), false);
        return runtimeView(
                scope,
                deployment,
                runtimeRow(scope, deployment.id(), false));
    }

    @Transactional(readOnly = true)
    public PortRuntimeView portRuntime(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String deploymentCode,
            int portNo) {
        if (portNo < 1 || portNo > 6) {
            throw notFound();
        }
        AuthorizedScope scope = authorize(
                platformPath,
                platformPath ? tenantCode : null,
                organizationCode,
                "device.read");
        String code = normalizeDeploymentCode(deploymentCode);
        DeploymentRow deployment = findDeployment(scope, code, false);
        RuntimeRow runtime = runtimeRow(
                scope, deployment.id(), false);
        PortRuntimeRow port = jdbc.query("""
                        SELECT p.id AS port_id, p.port_no,
                               state.delivery_door_state,
                               state.delivery_door_actuator_health,
                               state.delivery_door_contact_state,
                               state.clean_lock_power_state,
                               state.clean_solenoid_health,
                               state.clean_door_inferred_state,
                               state.clean_door_state_basis,
                               state.weight_sensor_health,
                               state.infrared_value,
                               state.infrared_sensor_health,
                               state.smoke_state,
                               state.smoke_sensor_health,
                               state.safety_status,
                               state.last_observed_at,
                               state.lock_version,
                               snapshot.business_enabled,
                               snapshot.fullness_mode
                        FROM dev_port p
                        JOIN dev_port_runtime_state state
                          ON state.tenant_id = p.tenant_id
                         AND state.organization_id = p.organization_id
                         AND state.deployment_id = p.deployment_id
                         AND state.port_id = p.id
                        LEFT JOIN dev_port_config_snapshot snapshot
                          ON snapshot.config_version_id = ?
                         AND snapshot.port_id = p.id
                        WHERE p.tenant_id = ?
                          AND p.organization_id = ?
                          AND p.deployment_id = ?
                          AND p.port_no = ?
                        """,
                (rs, ignored) -> portRuntimeRow(rs),
                deployment.latestConfigurationId(),
                scope.tenantId(),
                scope.organizationId(),
                deployment.id(),
                portNo).stream().findFirst()
                .orElseThrow(TargetDeviceApplication::notFound);
        var business = portBusinessPort.find(
                scope.tenantCode(),
                scope.organizationCode(),
                code,
                portNo);
        LinkedHashSet<String> deliveryBlockers =
                new LinkedHashSet<>(baseBlockers(
                        scope, deployment, runtime, true));
        LinkedHashSet<String> cleaningBlockers =
                new LinkedHashSet<>(baseBlockers(
                        scope, deployment, runtime, true));
        if (!Boolean.TRUE.equals(port.businessEnabled())) {
            deliveryBlockers.add("PORT_DISABLED");
        }
        if (!"CLOSED".equals(port.deliveryDoorState())
                || !"CLOSED".equals(port.deliveryDoorContactState())) {
            deliveryBlockers.add("DOOR_NOT_CLOSED");
            cleaningBlockers.add("DOOR_NOT_CLOSED");
        }
        if (!"OK".equals(port.deliveryDoorActuatorHealth())
                || !"DEENERGIZED".equals(port.cleanLockPowerState())
                || !"OK".equals(port.cleanSolenoidHealth())
                || !"SAFE".equals(port.safetyStatus())) {
            deliveryBlockers.add("SAFETY_LOCKED");
            cleaningBlockers.add("SAFETY_LOCKED");
        }
        if (!"OK".equals(port.weightSensorHealth())
                || !sensorHealthy(port)) {
            deliveryBlockers.add("PORT_SENSOR_UNHEALTHY");
            cleaningBlockers.add("PORT_SENSOR_UNHEALTHY");
        }
        if (!business.currentBagPresent()) {
            deliveryBlockers.add("CURRENT_BAG_MISSING");
        }
        if (usesWeight(port.fullnessMode())
                && !"VALID".equals(business.baselineState())) {
            deliveryBlockers.add("WEIGHT_BASELINE_MISSING");
        }
        if ("FULL".equals(business.fullnessState())) {
            deliveryBlockers.add("PORT_FULL");
        }
        if ("PENDING".equals(business.detectionGate())
                || "IN_PROGRESS".equals(business.detectionGate())) {
            deliveryBlockers.add("DEVICE_BUSY");
            cleaningBlockers.add("DEVICE_BUSY");
        }
        if (business.cleanOperationActive()) {
            deliveryBlockers.add("PORT_CLEAN_OPERATION_ACTIVE");
            cleaningBlockers.add("PORT_CLEAN_OPERATION_ACTIVE");
        }
        PortBusinessSummary businessView = new PortBusinessSummary(
                business.currentBagPresent(),
                business.baselineState(),
                business.detectionGate(),
                business.fullnessState(),
                business.displayedFullnessPercent(),
                business.cleanOperationActive());
        return new PortRuntimeView(
                code,
                port.portNo(),
                port.deliveryDoorState(),
                port.deliveryDoorActuatorHealth(),
                port.deliveryDoorContactState(),
                port.cleanLockPowerState(),
                port.cleanSolenoidHealth(),
                "UNKNOWN",
                "NOT_OBSERVABLE",
                port.weightSensorHealth(),
                port.infraredValue(),
                port.infraredSensorHealth(),
                port.smokeState(),
                port.smokeSensorHealth(),
                port.safetyStatus(),
                businessView,
                deliveryBlockers.isEmpty(),
                cleaningBlockers.isEmpty(),
                List.copyOf(deliveryBlockers),
                List.copyOf(cleaningBlockers),
                port.lastObservedAt(),
                port.version());
    }

    private DeploymentView deploymentVersionCommand(
            UUID operationUid,
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String deploymentCode,
            DeploymentVersionCommand request,
            DeploymentMutation mutation) {
        AuthorizedScope scope = authorize(
                platformPath,
                platformPath ? tenantCode : null,
                organizationCode,
                "device.manage");
        if (request == null || request.expectedVersion() == null) {
            throw invalidRequest();
        }
        String code = normalizeDeploymentCode(deploymentCode);
        return command(
                operationUid,
                scope,
                mutation.actionCode(),
                "DEVICE_DEPLOYMENT",
                code,
                request,
                DeploymentView.class,
                () -> mutateDeployment(
                        scope,
                        code,
                        request.expectedVersion(),
                        request.reason(),
                        mutation,
                        null,
                        false));
    }

    private CommandResult<DeploymentView> mutateDeployment(
            AuthorizedScope scope,
            String deploymentCode,
            long expectedVersion,
            String reason,
            DeploymentMutation mutation,
            Long expectedConfigurationVersion,
            boolean acceptanceConfirmed) {
        DeploymentRow before = findDeployment(
                scope, deploymentCode, true);
        if (before.version() != expectedVersion) {
            throw versionConflict(before.version());
        }
        LocalDateTime now = databaseNow();
        switch (mutation) {
            case ACTIVATE -> {
                if ("ENABLED".equals(before.lifecycleStatus())) {
                    throw conflict(
                            "DEVICE.DEPLOYMENT_ALREADY_ENABLED",
                            "设备部署已经激活");
                }
                if (!Set.of("COMMISSIONING", "DISABLED")
                        .contains(before.lifecycleStatus())) {
                    throw conflict(
                            "DEVICE.DEPLOYMENT_NOT_ACTIVATABLE",
                            "当前生命周期不允许激活");
                }
                if (!acceptanceConfirmed) {
                    throw unprocessable(
                            "DEVICE.ACCEPTANCE_CONFIRMATION_REQUIRED",
                            "首次或再次激活必须明确确认验收");
                }
                if (!Objects.equals(
                        before.latestConfigurationVersion(),
                        expectedConfigurationVersion)) {
                    throw versionConflict(
                            before.latestConfigurationVersion() == null
                                    ? 0
                                    : before.latestConfigurationVersion());
                }
                List<String> blockers = activationBlockers(scope, before);
                if (!blockers.isEmpty()) {
                    throw new TargetApiException(
                            422,
                            "DEVICE.DEPLOYMENT_NOT_ACTIVATABLE",
                            "设备尚未满足真实激活条件",
                            false,
                            Map.of("blockers", blockers));
                }
                jdbc.update("""
                                UPDATE dev_device_deployment
                                SET lifecycle_status = 'ENABLED',
                                    enabled_at = COALESCE(enabled_at, ?),
                                    lock_version = lock_version + 1,
                                    updated_at = ?
                                WHERE id = ?
                                """,
                        now, now, before.id());
                recoverInitialCommissioningLock(scope, before, reason, now);
            }
            case DEACTIVATE -> {
                if ("DISABLED".equals(before.lifecycleStatus())) {
                    throw conflict(
                            "DEVICE.DEPLOYMENT_ALREADY_DISABLED",
                            "设备部署已经停用");
                }
                if (!"ENABLED".equals(before.lifecycleStatus())) {
                    throw conflict(
                            "DEVICE.DEPLOYMENT_NOT_ENABLED",
                            "只有已激活部署可以停用");
                }
                jdbc.update("""
                                UPDATE dev_device_deployment
                                SET lifecycle_status = 'DISABLED',
                                    business_enabled = 0,
                                    lock_version = lock_version + 1,
                                    updated_at = ?
                                WHERE id = ?
                                """,
                        now, before.id());
            }
            case ENABLE_BUSINESS -> {
                if (before.businessEnabled()) {
                    throw conflict(
                            "DEVICE.BUSINESS_SWITCH_ALREADY_ENABLED",
                            "后台经营开关已经开启");
                }
                List<String> blockers = baseBlockers(
                        scope,
                        before,
                        runtimeRow(scope, before.id(), true),
                        false);
                if (!blockers.isEmpty()) {
                    throw new TargetApiException(
                            422,
                            "DEVICE.DEPLOYMENT_NOT_ACTIVATABLE",
                            "设备尚未满足经营开启条件",
                            false,
                            Map.of("blockers", blockers));
                }
                jdbc.update("""
                                UPDATE dev_device_deployment
                                SET business_enabled = 1,
                                    lock_version = lock_version + 1,
                                    updated_at = ?
                                WHERE id = ?
                                """,
                        now, before.id());
            }
            case DISABLE_BUSINESS -> {
                if (!before.businessEnabled()) {
                    throw conflict(
                            "DEVICE.BUSINESS_SWITCH_ALREADY_DISABLED",
                            "后台经营开关已经关闭");
                }
                jdbc.update("""
                                UPDATE dev_device_deployment
                                SET business_enabled = 0,
                                    lock_version = lock_version + 1,
                                    updated_at = ?
                                WHERE id = ?
                                """,
                        now, before.id());
            }
        }
        DeploymentView after = findDeployment(
                scope, deploymentCode, false).view();
        return new CommandResult<>(
                after,
                deploymentAuditSnapshot(before.view()),
                deploymentAuditSnapshot(after),
                reason);
    }

    private void recoverInitialCommissioningLock(
            AuthorizedScope scope,
            DeploymentRow deployment,
            String reason,
            LocalDateTime now) {
        jdbc.update("""
                        UPDATE dev_device_fault_event
                        SET status = 'RECOVERED',
                            recovery_source_kind = ?,
                            recovery_method = 'INITIAL_ACCEPTANCE',
                            recovered_at = ?,
                            recovered_by_staff_account_id = ?,
                            recovery_reason = ?,
                            lock_version = lock_version + 1
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND fault_code = 'INITIAL_COMMISSIONING'
                          AND status = 'OPEN'
                        """,
                scope.staffAccountId() == null
                        ? "SYSTEM_VERIFIED"
                        : "STAFF_CONFIRMED",
                now,
                scope.staffAccountId(),
                blankToNull(reason) == null
                        ? "initial commissioning accepted"
                        : blankToNull(reason),
                scope.tenantId(),
                scope.organizationId(),
                deployment.id());
        jdbc.update("""
                        UPDATE dev_deployment_runtime_state
                        SET safety_status = 'SAFE',
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                        """,
                now,
                scope.tenantId(),
                scope.organizationId(),
                deployment.id());
        jdbc.update("""
                        UPDATE dev_port_runtime_state
                        SET safety_status = 'SAFE',
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND safety_status = 'SAFETY_BLOCKED'
                        """,
                now,
                scope.tenantId(),
                scope.organizationId(),
                deployment.id());
    }

    private List<String> activationBlockers(
            AuthorizedScope scope,
            DeploymentRow deployment) {
        RuntimeRow runtime = runtimeRow(scope, deployment.id(), true);
        LinkedHashSet<String> blockers = new LinkedHashSet<>();
        if (!scope.tenantEnabled()) {
            blockers.add("TENANT_DISABLED");
        }
        if (!scope.organizationEnabled()) {
            blockers.add("ORGANIZATION_DISABLED");
        }
        if (!configurationReady(scope, deployment, runtime)) {
            blockers.add("CONFIGURATION_NOT_APPLIED");
        }
        if (!trustedOrangePiRuntimeFresh(
                scope, deployment.id())) {
            blockers.add("TRUSTED_DEVICE_IDENTITY_UNPROVEN");
        }
        if (!"ONLINE".equals(runtime.edgeConnectionStatus())) {
            blockers.add("EDGE_OFFLINE");
        }
        if ("SAFETY_BLOCKED".equals(runtime.safetyStatus())
                || "OPERATION_BLOCKED".equals(
                runtime.safetyStatus())) {
            blockers.add("SAFETY_LOCKED");
        }
        Integer seriousFaults = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_device_fault_event
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND status = 'OPEN'
                          AND fault_code <> 'INITIAL_COMMISSIONING'
                          AND impact_level IN (
                              'BUSINESS_BLOCKING',
                              'SAFETY_BLOCKING'
                          )
                        """,
                Integer.class,
                scope.tenantId(),
                scope.organizationId(),
                deployment.id());
        if (seriousFaults != null && seriousFaults > 0) {
            blockers.add("SAFETY_LOCKED");
        }
        if (occupied(deployment.assetId())) {
            blockers.add("DEVICE_BUSY");
        }
        return List.copyOf(blockers);
    }

    private DeploymentRuntimeView runtimeView(
            AuthorizedScope scope,
            DeploymentRow deployment,
            RuntimeRow runtime) {
        List<String> delivery = baseBlockers(
                scope, deployment, runtime, true);
        List<String> cleaning = baseBlockers(
                scope, deployment, runtime, true);
        RuntimeConfigurationSummary configuration =
                new RuntimeConfigurationSummary(
                        deployment.latestConfigurationVersion(),
                        deployment.appliedConfigurationVersion(),
                        deployment.configurationApplicationStatus(),
                        configurationReady(
                                scope, deployment, runtime));
        RuntimeHealthSummary health = new RuntimeHealthSummary(
                runtime.edgeConnectionStatus(),
                runtime.mcuLinkStatus(),
                runtime.safetyStatus(),
                runtime.aggregateWeightHealth(),
                runtime.cameraHealth(),
                runtime.localStorageHealth(),
                runtime.clockSyncHealth(),
                runtime.edgeSoftwareVersion(),
                runtime.mcuFirmwareVersion(),
                runtime.uartState(),
                runtime.uartProtocolMajor(),
                runtime.uartProtocolMinor(),
                runtime.capabilityBitmapHex(),
                runtime.lastHeartbeatAt(),
                runtime.lastDeviceEventAt(),
                runtime.version());
        return new DeploymentRuntimeView(
                deployment.publicCode(),
                deployment.lifecycleStatus(),
                deployment.businessEnabled(),
                deployment.version(),
                configuration,
                health,
                occupied(deployment.assetId()),
                delivery.isEmpty(),
                cleaning.isEmpty(),
                delivery,
                cleaning);
    }

    private List<String> baseBlockers(
            AuthorizedScope scope,
            DeploymentRow deployment,
            RuntimeRow runtime,
            boolean requireBusinessSwitch) {
        LinkedHashSet<String> blockers = new LinkedHashSet<>();
        if (!scope.tenantEnabled()) {
            blockers.add("TENANT_DISABLED");
        }
        if (!scope.organizationEnabled()) {
            blockers.add("ORGANIZATION_DISABLED");
        }
        if (!"ENABLED".equals(deployment.lifecycleStatus())) {
            blockers.add("DEPLOYMENT_NOT_ENABLED");
        }
        if (requireBusinessSwitch && !deployment.businessEnabled()) {
            blockers.add("BUSINESS_SWITCH_DISABLED");
        }
        if (!configurationReady(scope, deployment, runtime)) {
            blockers.add("CONFIGURATION_NOT_APPLIED");
        }
        if (!trustedOrangePiRuntimeFresh(scope, deployment.id())
                || !"ONLINE".equals(
                runtime.edgeConnectionStatus())) {
            blockers.add("EDGE_OFFLINE");
        }
        if ("SAFETY_BLOCKED".equals(runtime.safetyStatus())
                || "OPERATION_BLOCKED".equals(
                runtime.safetyStatus())) {
            blockers.add("SAFETY_LOCKED");
        }
        if (occupied(deployment.assetId())) {
            blockers.add("DEVICE_BUSY");
        }
        return List.copyOf(blockers);
    }

    private boolean configurationReady(
            AuthorizedScope scope,
            DeploymentRow deployment,
            RuntimeRow runtime) {
        if (deployment.latestConfigurationVersion() == null
                || !"APPLIED".equals(
                deployment.configurationApplicationStatus())
                || !Objects.equals(
                deployment.latestConfigurationVersion(),
                deployment.appliedConfigurationVersion())
                || !Objects.equals(
                deployment.latestConfigurationVersion(),
                runtime.orangePiReportedConfigurationVersion())) {
            return false;
        }
        Integer exact = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_config_version version
                        WHERE version.tenant_id = ?
                          AND version.organization_id = ?
                          AND version.deployment_id = ?
                          AND version.id = ?
                          AND version.version_no = ?
                          AND version.content_sha256 = ?
                          AND version.mcu_payload_sha256 = ?
                        """,
                Integer.class,
                scope.tenantId(),
                scope.organizationId(),
                deployment.id(),
                deployment.latestConfigurationId(),
                runtime.orangePiReportedConfigurationVersion(),
                runtime.orangePiReportedContentSha256(),
                runtime.orangePiReportedMcuPayloadSha256());
        return exact != null && exact == 1;
    }

    private boolean trustedOrangePiRuntimeFresh(
            AuthorizedScope scope, long deploymentId) {
        Integer fresh = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_deployment_runtime_state runtime
                        JOIN dev_config_version config
                          ON config.tenant_id = runtime.tenant_id
                         AND config.organization_id =
                             runtime.organization_id
                         AND config.deployment_id =
                             runtime.deployment_id
                         AND config.version_no =
                             runtime.orange_pi_reported_config_version_no
                        WHERE runtime.tenant_id = ?
                          AND runtime.organization_id = ?
                          AND runtime.deployment_id = ?
                          AND runtime.trusted_runtime_edge_event_id
                              IS NOT NULL
                          AND runtime.trusted_runtime_received_at
                              IS NOT NULL
                          AND TIMESTAMPDIFF(
                              MICROSECOND,
                              runtime.trusted_runtime_received_at,
                              UTC_TIMESTAMP(3)
                          ) BETWEEN 0 AND LEAST(
                              config.edge_heartbeat_interval_ms
                                  * config.edge_heartbeat_miss_threshold
                                  * 1000,
                              86400000000
                          )
                        """,
                Integer.class,
                scope.tenantId(),
                scope.organizationId(),
                deploymentId);
        return fresh != null && fresh == 1;
    }

    private static boolean sensorHealthy(PortRuntimeRow port) {
        boolean infrared = "OK".equals(port.infraredSensorHealth())
                && Set.of("CLEAR", "BLOCKED")
                .contains(port.infraredValue());
        boolean smoke = "OK".equals(port.smokeSensorHealth())
                && Set.of("NORMAL", "ALARM").contains(port.smokeState());
        return infrared && smoke;
    }

    private static boolean usesWeight(String fullnessMode) {
        return "WEIGHT_ONLY".equals(fullnessMode)
                || "INFRARED_OR_WEIGHT".equals(fullnessMode);
    }

    @Transactional(readOnly = true)
    public CursorPage<ConfigurationVersionSummary> configurationVersions(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String deploymentCode,
            Long beforeVersionNo,
            int requestedLimit) {
        if (beforeVersionNo != null && beforeVersionNo < 1) {
            throw invalidRequest();
        }
        AuthorizedScope scope = authorize(
                platformPath,
                platformPath ? tenantCode : null,
                organizationCode,
                "device.read");
        DeploymentRow deployment = findDeployment(
                scope, normalizeDeploymentCode(deploymentCode), false);
        int limit = requestedLimit <= 0
                ? DEFAULT_CURSOR_LIMIT
                : Math.min(requestedLimit, MAX_CURSOR_LIMIT);
        String beforePredicate = beforeVersionNo == null
                ? ""
                : " AND config.version_no < ?";
        List<Object> parameters = new ArrayList<>();
        parameters.add(scope.tenantId());
        parameters.add(scope.organizationId());
        parameters.add(deployment.id());
        if (beforeVersionNo != null) {
            parameters.add(beforeVersionNo);
        }
        parameters.add(limit + 1);
        List<ConfigurationVersionRow> rows = jdbc.query("""
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
                               LOWER(HEX(config.content_sha256))
                                   AS content_sha256,
                               LOWER(HEX(config.mcu_payload_sha256))
                                   AS mcu_payload_sha256,
                               config.publication_source,
                               staff.display_name AS publisher_name,
                               config.published_at,
                               app.application_uid, app.status AS app_status,
                               app.lock_version AS app_version
                        FROM dev_config_version config
                        LEFT JOIN iam_staff_account staff
                          ON staff.tenant_id = config.tenant_id
                         AND staff.id =
                             config.published_by_staff_account_id
                        JOIN dev_config_application app
                          ON app.tenant_id = config.tenant_id
                         AND app.organization_id = config.organization_id
                         AND app.deployment_id = config.deployment_id
                         AND app.config_version_id = config.id
                        WHERE config.tenant_id = ?
                          AND config.organization_id = ?
                          AND config.deployment_id = ?
                        """
                        + beforePredicate
                        + " ORDER BY config.version_no DESC LIMIT ?",
                (rs, ignored) -> configurationVersionRow(rs),
                parameters.toArray());
        boolean hasMore = rows.size() > limit;
        List<ConfigurationVersionRow> visible = hasMore
                ? rows.subList(0, limit)
                : rows;
        List<ConfigurationVersionSummary> items = visible.stream()
                .map(this::configurationSummary)
                .toList();
        Long next = hasMore && !visible.isEmpty()
                ? visible.getLast().versionNo()
                : null;
        return new CursorPage<>(items, next);
    }

    @Transactional(readOnly = true)
    public ConfigurationVersionView configurationVersion(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String deploymentCode,
            long versionNo) {
        if (versionNo < 1) {
            throw notFound();
        }
        AuthorizedScope scope = authorize(
                platformPath,
                platformPath ? tenantCode : null,
                organizationCode,
                "device.read");
        DeploymentRow deployment = findDeployment(
                scope, normalizeDeploymentCode(deploymentCode), false);
        ConfigurationVersionRow configuration =
                findConfigurationVersion(
                        scope, deployment.id(), versionNo)
                        .orElseThrow(TargetDeviceApplication::notFound);
        List<ConfigurationPortSnapshot> ports =
                configurationPorts(scope, deployment.id(), configuration.id());
        return configurationView(
                deployment.publicCode(), configuration, ports);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public ConfigurationAcceptedView releaseConfiguration(
            UUID operationUid,
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String deploymentCode,
            ConfigurationReleaseRequest request) {
        AuthorizedScope scope = authorize(
                platformPath,
                platformPath ? tenantCode : null,
                organizationCode,
                "device.configuration.manage");
        if (request == null
                || request.expectedLatestVersion() == null
                || request.locationCorrectionConfirmed() == null) {
            throw invalidRequest();
        }
        String code = normalizeDeploymentCode(deploymentCode);
        String statusUrl = applicationCollectionUrl(
                platformPath,
                scope.tenantCode(),
                scope.organizationCode(),
                code);
        return command(
                operationUid,
                scope,
                "device.configuration.release",
                "DEVICE_CONFIGURATION",
                code,
                request,
                ConfigurationAcceptedView.class,
                () -> releaseConfiguration(
                        operationUid,
                        scope,
                        code,
                        statusUrl,
                        request));
    }

    @Transactional(readOnly = true)
    public ConfigurationApplicationView configurationApplication(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String deploymentCode,
            UUID applicationUid) {
        AuthorizedScope scope = authorize(
                platformPath,
                platformPath ? tenantCode : null,
                organizationCode,
                "device.read");
        DeploymentRow deployment = findDeployment(
                scope, normalizeDeploymentCode(deploymentCode), false);
        ApplicationRow application = findApplication(
                scope, deployment.id(), applicationUid, false)
                .orElseThrow(TargetDeviceApplication::applicationNotFound);
        return applicationView(application);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public ConfigurationAcceptedView resynchronizeConfiguration(
            UUID operationUid,
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String deploymentCode,
            UUID applicationUid,
            DeploymentVersionCommand request) {
        AuthorizedScope scope = authorize(
                platformPath,
                platformPath ? tenantCode : null,
                organizationCode,
                "device.configuration.manage");
        if (request == null || request.expectedVersion() == null) {
            throw invalidRequest();
        }
        String code = normalizeDeploymentCode(deploymentCode);
        String target = code + "|application:" + applicationUid;
        String statusUrl = applicationCollectionUrl(
                platformPath,
                scope.tenantCode(),
                scope.organizationCode(),
                code) + "/" + applicationUid;
        return command(
                operationUid,
                scope,
                "device.configuration.resynchronize",
                CONFIGURATION_TARGET_TYPE,
                target,
                request,
                ConfigurationAcceptedView.class,
                () -> resynchronize(
                        operationUid,
                        scope,
                        code,
                        applicationUid,
                        statusUrl,
                        request));
    }

    private CommandResult<ConfigurationAcceptedView> releaseConfiguration(
            UUID operationUid,
            AuthorizedScope scope,
            String deploymentCode,
            String applicationBaseUrl,
            ConfigurationReleaseRequest request) {
        DeploymentRow deployment = findDeployment(
                scope, deploymentCode, true);
        long latestVersion = deployment.latestConfigurationVersion() == null
                ? 0 : deployment.latestConfigurationVersion();
        if (latestVersion != request.expectedLatestVersion()) {
            throw versionConflict(latestVersion);
        }
        var normalized = canonicalizer.normalize(
                request, deployment.portCount());
        Optional<ConfigurationVersionRow> previous =
                latestVersion == 0
                        ? Optional.empty()
                        : findConfigurationVersion(
                        scope, deployment.id(), latestVersion);
        if (previous.isPresent()
                && previous.get().contentSha256()
                .equals(normalized.contentSha256Hex())) {
            throw unprocessable(
                    "DEVICE.CONFIGURATION_UNCHANGED",
                    "完整配置内容与当前最高版本相同");
        }
        if (previous.isPresent()
                && locationChanged(
                previous.get(), normalized.device())
                && !request.locationCorrectionConfirmed()) {
            throw unprocessable(
                    "DEVICE.LOCATION_CORRECTION_CONFIRMATION_REQUIRED",
                    "非首次修改地址或坐标必须明确确认位置纠正");
        }
        long versionNo = latestVersion + 1;
        byte[] mcuPayloadSha256 = canonicalizer.mcuPayloadSha256(
                versionNo, normalized);
        LocalDateTime now = databaseNow();
        String publicationSource =
                scope.platformActor() ? "SYSTEM" : "STAFF";
        long configurationId = insertAndReturnKey("""
                INSERT INTO dev_config_version (
                    tenant_id, organization_id, deployment_id,
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
                    ?, ?, ?, ?, ?, ?
                )
                """,
                scope.tenantId(),
                scope.organizationId(),
                deployment.id(),
                versionNo,
                DeviceConfigurationCanonicalizer
                        .CONFIGURATION_SCHEMA_VERSION,
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
                publicationSource,
                scope.staffAccountId(),
                now,
                now);
        Map<Integer, Long> portIds = jdbc.query("""
                        SELECT id, port_no
                        FROM dev_port
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                        ORDER BY port_no
                        """,
                rs -> {
                    Map<Integer, Long> result = new LinkedHashMap<>();
                    while (rs.next()) {
                        result.put(
                                rs.getInt("port_no"),
                                rs.getLong("id"));
                    }
                    return result;
                },
                scope.tenantId(),
                scope.organizationId(),
                deployment.id());
        for (var port : normalized.ports()) {
            ConfigurationPortSnapshot view = port.view();
            Long portId = portIds.get(view.portNo());
            if (portId == null) {
                throw invariant();
            }
            jdbc.update("""
                            INSERT INTO dev_port_config_snapshot (
                                tenant_id, organization_id, deployment_id,
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
                    deployment.id(),
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
                    now);
        }
        UUID applicationUid = UUID.randomUUID();
        long applicationId = insertAndReturnKey("""
                INSERT INTO dev_config_application (
                    application_uid, tenant_id, organization_id,
                    deployment_id, config_version_id, status,
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
                deployment.id(),
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
        Map<String, Object> commandEnvelope = new LinkedHashMap<>();
        commandEnvelope.put("schemaVersion", 1);
        commandEnvelope.put("commandUid", commandUid.toString());
        commandEnvelope.put("commandType", "APPLY_CONFIGURATION");
        commandEnvelope.put("deploymentCode", deployment.publicCode());
        commandEnvelope.put("target", Map.of(
                "type", CONFIGURATION_TARGET_TYPE,
                "uid", applicationUid.toString()));
        commandEnvelope.put("issuedAt", issuedAt.toString());
        commandEnvelope.put(
                "expiresAt",
                issuedAt.plusSeconds(
                        CONFIGURATION_COMMAND_VALIDITY_SECONDS).toString());
        commandEnvelope.put("payloadSchemaVersion", 1);
        commandEnvelope.put(
                "payloadSha256",
                canonicalizer.hex(payloadSha256));
        commandEnvelope.put("payload", payload);
        commandEnvelope.put("cosGrant", null);
        byte[] commandEnvelopeSha256 =
                canonicalizer.payloadSha256(commandEnvelope);
        String commandEnvelopeJson = writeJson(commandEnvelope);
        long commandId = insertAndReturnKey("""
                INSERT INTO dev_device_command (
                    command_uid, tenant_id, organization_id,
                    deployment_id, command_type, delivery_session_id,
                    clean_operation_id, config_application_id,
                    fullness_detection_id, baseline_measurement_id,
                    payload_schema_version, semantic_payload,
                    semantic_payload_sha256, physical_state, queued_at,
                    edge_accepted_at, physical_started_at,
                    physical_ended_at, lock_version, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, 'APPLY_CONFIGURATION',
                    NULL, NULL, ?, NULL, NULL,
                    1, CAST(? AS JSON), ?, 'QUEUED', ?,
                    NULL, NULL, NULL, 0, ?, ?
                )
                """,
                commandUid.toString(),
                scope.tenantId(),
                scope.organizationId(),
                deployment.id(),
                applicationId,
                commandEnvelopeJson,
                commandEnvelopeSha256,
                now,
                now,
                now);
        Map<String, Object> taskSnapshot = new LinkedHashMap<>();
        taskSnapshot.put("schemaVersion", 1);
        taskSnapshot.put("commandUid", commandUid.toString());
        taskSnapshot.put("commandType", "APPLY_CONFIGURATION");
        taskSnapshot.put("hardwareSn", deployment.hardwareSn());
        taskSnapshot.put("deploymentCode", deployment.publicCode());
        taskSnapshot.put("target", Map.of(
                "type", CONFIGURATION_TARGET_TYPE,
                "uid", applicationUid.toString()));
        taskSnapshot.put("payloadSchemaVersion", 1);
        taskSnapshot.put(
                "semanticPayloadSha256",
                canonicalizer.hex(commandEnvelopeSha256));
        taskRegistrationPort.register(new ReliableDeviceTaskRegistration(
                CONFIGURATION_TASK_TYPE,
                CONFIGURATION_TASK_TYPE + ":"
                        + applicationUid.toString().toUpperCase(Locale.ROOT),
                CONFIGURATION_TARGET_TYPE,
                applicationUid.toString(),
                taskRefFactory.issue(
                        scope.tenantId(),
                        scope.organizationId(),
                        deployment.id(),
                        commandId),
                1,
                writeJson(taskSnapshot),
                commandEnvelopeSha256,
                operationUid,
                null,
                12,
                true));
        String statusUrl = applicationBaseUrl + "/" + applicationUid;
        ConfigurationAcceptedView response =
                new ConfigurationAcceptedView(
                        operationUid,
                        applicationUid,
                        applicationUid,
                        versionNo,
                        normalized.contentSha256Hex(),
                        canonicalizer.hex(mcuPayloadSha256),
                        "PENDING",
                        "PENDING",
                        statusUrl,
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

    private CommandResult<ConfigurationAcceptedView> resynchronize(
            UUID operationUid,
            AuthorizedScope scope,
            String deploymentCode,
            UUID applicationUid,
            String statusUrl,
            DeploymentVersionCommand request) {
        DeploymentRow deployment = findDeployment(
                scope, deploymentCode, true);
        ApplicationRow application = findApplication(
                scope, deployment.id(), applicationUid, true)
                .orElseThrow(TargetDeviceApplication::applicationNotFound);
        if (application.version() != request.expectedVersion()) {
            throw versionConflict(application.version());
        }
        if (application.versionNo()
                != deployment.latestConfigurationVersion()) {
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
                    "配置应用仍在正常收敛，当前不允许重同步");
        }
        LocalDateTime now = databaseNow();
        jdbc.update("""
                        UPDATE dev_config_application
                        SET status = CASE
                                WHEN status = 'FAILED'
                                THEN 'PENDING'
                                ELSE status
                            END,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                        """,
                now,
                application.id());
        taskWakePort.wake(new ReliableTaskWake(
                task.taskUid(), "WEB_RESYNCHRONIZATION"));
        ApplicationRow after = findApplication(
                scope, deployment.id(), applicationUid, false)
                .orElseThrow(TargetDeviceApplication::invariant);
        ReliableDeviceTaskStatus afterTask = taskStatusPort.find(
                        CONFIGURATION_TASK_TYPE,
                        CONFIGURATION_TARGET_TYPE,
                        applicationUid.toString(),
                        false)
                .orElseThrow(TargetDeviceApplication::invariant);
        ConfigurationAcceptedView response =
                new ConfigurationAcceptedView(
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
            String deploymentCode,
            ConfigurationVersionRow row,
            List<ConfigurationPortSnapshot> ports) {
        return new ConfigurationVersionView(
                deploymentCode,
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

    private AuthorizedScope authorize(
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
                (tenantKey,
                 organizationKey,
                 platformAdminKey,
                 staffAccountKey) -> {
                    keys[0] = tenantKey;
                    keys[1] = organizationKey;
                    keys[2] = platformAdminKey;
                    keys[3] = staffAccountKey;
                });
        return new AuthorizedScope(
                authorization.platformActor(),
                authorization.principalUid(),
                authorization.sessionUid(),
                authorization.actorDisplayName(),
                authorization.tenantCode(),
                authorization.organizationCode(),
                authorization.tenantEnabled(),
                authorization.organizationEnabled(),
                keys[0],
                keys[1],
                keys[2],
                keys[3]);
    }

    private <T> T command(
            UUID operationUid,
            AuthorizedScope scope,
            String actionCode,
            String targetType,
            String targetStableKey,
            Object request,
            Class<T> responseType,
            Supplier<CommandResult<T>> work) {
        validateOperationUid(operationUid);
        TargetWebAuditRequestContext.describe(
                actionCode, targetStableKey);
        String fingerprint = fingerprint(
                scope.principalUid(),
                actionCode,
                targetStableKey,
                request);
        Optional<SuccessfulAudit> previous =
                auditPort.findSuccessful(operationUid);
        if (previous.isPresent()) {
            SuccessfulAudit audit = previous.get();
            JsonNode summary = readJson(
                    audit.safeChangeSummaryJson());
            boolean sameActor = scope.platformActor()
                    ? audit.actorKind()
                    == AuditActorKind.PLATFORM_ADMIN
                    && Objects.equals(
                    audit.platformAdminId(),
                    scope.platformAdminId())
                    : audit.actorKind()
                    == AuditActorKind.STAFF_ACCOUNT
                    && Objects.equals(
                    audit.staffAccountId(),
                    scope.staffAccountId());
            if (!sameActor
                    || !actionCode.equals(audit.actionCode())
                    || !targetType.equals(audit.targetType())
                    || !targetStableKey.equals(audit.targetStableKey())
                    || !fingerprint.equals(
                    summary.path("fingerprint").asText())) {
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
        summary.put("reasonPresent",
                result.reason() != null
                        && !result.reason().isBlank());
        AuditScopeKind auditScope = scope.organizationId() != null
                ? AuditScopeKind.ORGANIZATION
                : scope.tenantId() != null
                ? AuditScopeKind.TENANT
                : AuditScopeKind.PLATFORM;
        auditPort.append(new AuditEntry(
                UUID.randomUUID(),
                UUID.randomUUID(),
                operationUid,
                auditScope,
                scope.tenantId(),
                scope.organizationId(),
                scope.platformActor()
                        ? AuditActorKind.PLATFORM_ADMIN
                        : AuditActorKind.STAFF_ACCOUNT,
                scope.platformAdminId(),
                scope.staffAccountId(),
                null,
                null,
                scope.actorDisplayName(),
                actionCode,
                targetType,
                targetStableKey,
                "WEB",
                "SUCCEEDED",
                scope.sessionUid(),
                blankToNull(result.reason()),
                writeJson(summary),
                Instant.now()));
        return result.response();
    }

    private Optional<DeviceAssetView> findAssetView(String hardwareSn) {
        return jdbc.query("""
                        SELECT a.id, a.hardware_sn, a.model_name,
                               a.production_batch, a.expected_port_count,
                               a.lifecycle_status, a.lock_version,
                               a.created_at, a.updated_at,
                               d.public_code AS deployment_code,
                               d.lifecycle_status AS deployment_status,
                               d.business_enabled,
                               t.tenant_code, o.organization_code
                        FROM dev_device_asset a
                        LEFT JOIN dev_asset_active_deployment active
                          ON active.asset_id = a.id
                        LEFT JOIN dev_device_deployment d
                          ON d.id = active.deployment_id
                        LEFT JOIN iam_tenant t ON t.id = active.tenant_id
                        LEFT JOIN iam_organization o
                          ON o.tenant_id = active.tenant_id
                         AND o.id = active.organization_id
                        WHERE a.hardware_sn = ?
                        """,
                (rs, ignored) -> assetView(rs),
                hardwareSn).stream().findFirst();
    }

    private DeviceAssetView assetView(ResultSet rs)
            throws SQLException {
        String deploymentCode = rs.getString("deployment_code");
        CurrentDeploymentSummary deployment =
                deploymentCode == null
                        ? null
                        : new CurrentDeploymentSummary(
                        deploymentCode,
                        rs.getString("tenant_code"),
                        rs.getString("organization_code"),
                        rs.getString("deployment_status"),
                        rs.getBoolean("business_enabled"));
        String hardwareSn = rs.getString("hardware_sn");
        return new DeviceAssetView(
                hardwareSn,
                rs.getString("model_name"),
                rs.getString("production_batch"),
                rs.getInt("expected_port_count"),
                rs.getString("lifecycle_status"),
                deployment,
                rs.getLong("lock_version"),
                instant(rs, "created_at"),
                instant(rs, "updated_at"),
                new ComputedOneNetMapping(
                        oneNetProductId,
                        hardwareSn,
                        true));
    }

    private AssetRow lockAsset(String hardwareSn) {
        return jdbc.query("""
                        SELECT id, hardware_sn, model_name,
                               expected_port_count, lifecycle_status,
                               lock_version
                        FROM dev_device_asset
                        WHERE hardware_sn = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new AssetRow(
                        rs.getLong("id"),
                        rs.getString("hardware_sn"),
                        rs.getString("model_name"),
                        rs.getInt("expected_port_count"),
                        rs.getString("lifecycle_status"),
                        rs.getLong("lock_version")),
                hardwareSn).stream().findFirst()
                .orElseThrow(TargetDeviceApplication::assetNotFound);
    }

    private DeploymentRow findDeployment(
            AuthorizedScope scope,
            String deploymentCode,
            boolean forUpdate) {
        if (scope.tenantId() == null || scope.organizationId() == null) {
            throw notFound();
        }
        if (forUpdate) {
            boolean found = !jdbc.query("""
                            SELECT d.id
                            FROM dev_device_deployment d
                            JOIN dev_device_asset a ON a.id = d.asset_id
                            WHERE d.tenant_id = ?
                              AND d.organization_id = ?
                              AND d.public_code = ?
                            FOR UPDATE
                            """,
                    (rs, ignored) -> rs.getLong("id"),
                    scope.tenantId(),
                    scope.organizationId(),
                    deploymentCode).isEmpty();
            if (!found) {
                throw notFound();
            }
        }
        return jdbc.query(
                        deploymentSelect()
                                + " FROM dev_device_deployment d "
                                + deploymentJoins()
                                + """
                                WHERE d.tenant_id = ?
                                  AND d.organization_id = ?
                                  AND d.public_code = ?
                                """,
                        (rs, ignored) -> deploymentRow(rs),
                        scope.tenantId(),
                        scope.organizationId(),
                        deploymentCode).stream().findFirst()
                .orElseThrow(TargetDeviceApplication::notFound);
    }

    private static String deploymentSelect() {
        return """
                SELECT d.id, d.tenant_id, d.organization_id, d.asset_id,
                       d.public_code, d.lifecycle_status,
                       d.business_enabled, d.commissioned_at, d.enabled_at,
                       d.created_at, d.updated_at, d.lock_version,
                       a.hardware_sn, a.model_name, a.expected_port_count,
                       a.lifecycle_status AS asset_status,
                       a.lock_version AS asset_version,
                       t.tenant_code, t.status AS tenant_status,
                       o.organization_code,
                       o.status AS organization_status,
                       (SELECT COUNT(*) FROM dev_port p
                        WHERE p.deployment_id = d.id) AS port_count,
                       latest.id AS latest_configuration_id,
                       latest.version_no AS latest_configuration_version,
                       app.status AS configuration_application_status,
                       applied.version_no AS applied_configuration_version,
                       runtime.edge_connection_status
                """;
    }

    private static String deploymentJoins() {
        return """
                JOIN dev_device_asset a ON a.id = d.asset_id
                JOIN iam_tenant t ON t.id = d.tenant_id
                JOIN iam_organization o
                  ON o.tenant_id = d.tenant_id
                 AND o.id = d.organization_id
                LEFT JOIN dev_config_version latest
                  ON latest.id = (
                      SELECT latest_lookup.id
                      FROM dev_config_version latest_lookup
                      WHERE latest_lookup.tenant_id = d.tenant_id
                        AND latest_lookup.organization_id =
                            d.organization_id
                        AND latest_lookup.deployment_id = d.id
                      ORDER BY latest_lookup.version_no DESC
                      LIMIT 1
                  )
                LEFT JOIN dev_config_application app
                  ON app.config_version_id = latest.id
                LEFT JOIN dev_config_version applied
                  ON applied.id = (
                      SELECT applied_lookup.id
                      FROM dev_config_version applied_lookup
                      JOIN dev_config_application applied_app
                        ON applied_app.config_version_id =
                            applied_lookup.id
                       AND applied_app.status = 'APPLIED'
                      WHERE applied_lookup.tenant_id = d.tenant_id
                        AND applied_lookup.organization_id =
                            d.organization_id
                        AND applied_lookup.deployment_id = d.id
                      ORDER BY applied_lookup.version_no DESC
                      LIMIT 1
                  )
                LEFT JOIN dev_deployment_runtime_state runtime
                  ON runtime.tenant_id = d.tenant_id
                 AND runtime.organization_id = d.organization_id
                 AND runtime.deployment_id = d.id
                """;
    }

    private DeploymentRow deploymentRow(ResultSet rs)
            throws SQLException {
        DeploymentAssetSummary asset = new DeploymentAssetSummary(
                rs.getString("hardware_sn"),
                rs.getString("model_name"),
                rs.getInt("expected_port_count"),
                rs.getString("asset_status"),
                rs.getLong("asset_version"));
        DeploymentView view = new DeploymentView(
                rs.getString("public_code"),
                rs.getString("tenant_code"),
                rs.getString("organization_code"),
                asset,
                rs.getString("lifecycle_status"),
                rs.getBoolean("business_enabled"),
                rs.getInt("port_count"),
                nullableLong(rs, "latest_configuration_version"),
                nullableLong(rs, "applied_configuration_version"),
                rs.getString("configuration_application_status"),
                rs.getString("edge_connection_status"),
                rs.getLong("lock_version"),
                nullableInstant(rs, "commissioned_at"),
                nullableInstant(rs, "enabled_at"),
                instant(rs, "created_at"),
                instant(rs, "updated_at"));
        return new DeploymentRow(
                rs.getLong("id"),
                rs.getLong("asset_id"),
                rs.getString("public_code"),
                rs.getString("hardware_sn"),
                rs.getString("lifecycle_status"),
                rs.getBoolean("business_enabled"),
                rs.getInt("port_count"),
                nullableLong(rs, "latest_configuration_id"),
                nullableLong(rs, "latest_configuration_version"),
                nullableLong(rs, "applied_configuration_version"),
                rs.getString("configuration_application_status"),
                rs.getLong("lock_version"),
                view);
    }

    private RuntimeRow runtimeRow(
            AuthorizedScope scope,
            long deploymentId,
            boolean forUpdate) {
        String lock = forUpdate ? " FOR UPDATE" : "";
        return jdbc.query("""
                        SELECT edge_connection_status, mcu_link_status,
                               safety_status, aggregate_weight_health,
                               camera_health, local_storage_health,
                               clock_sync_health, edge_software_version,
                               mcu_firmware_version, uart_state,
                               uart_protocol_major, uart_protocol_minor,
                               capability_bitmap_hex,
                               applied_config_version_no,
                               orange_pi_reported_config_version_no,
                               orange_pi_reported_config_content_sha256,
                               orange_pi_reported_config_mcu_payload_sha256,
                               last_heartbeat_at, last_device_event_at,
                               lock_version
                        FROM dev_deployment_runtime_state
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                        """ + lock,
                (rs, ignored) -> new RuntimeRow(
                        rs.getString("edge_connection_status"),
                        rs.getString("mcu_link_status"),
                        rs.getString("safety_status"),
                        rs.getString("aggregate_weight_health"),
                        rs.getString("camera_health"),
                        rs.getString("local_storage_health"),
                        rs.getString("clock_sync_health"),
                        rs.getString("edge_software_version"),
                        rs.getString("mcu_firmware_version"),
                        rs.getString("uart_state"),
                        nullableInteger(rs, "uart_protocol_major"),
                        nullableInteger(rs, "uart_protocol_minor"),
                        rs.getString("capability_bitmap_hex"),
                        nullableLong(rs, "applied_config_version_no"),
                        nullableLong(
                                rs,
                                "orange_pi_reported_config_version_no"),
                        rs.getBytes(
                                "orange_pi_reported_config_content_sha256"),
                        rs.getBytes(
                                "orange_pi_reported_config_mcu_payload_sha256"),
                        nullableInstant(rs, "last_heartbeat_at"),
                        nullableInstant(rs, "last_device_event_at"),
                        rs.getLong("lock_version")),
                scope.tenantId(),
                scope.organizationId(),
                deploymentId).stream().findFirst()
                .orElseThrow(TargetDeviceApplication::invariant);
    }

    private static PortRuntimeRow portRuntimeRow(ResultSet rs)
            throws SQLException {
        return new PortRuntimeRow(
                rs.getInt("port_no"),
                rs.getString("delivery_door_state"),
                rs.getString("delivery_door_actuator_health"),
                rs.getString("delivery_door_contact_state"),
                rs.getString("clean_lock_power_state"),
                rs.getString("clean_solenoid_health"),
                rs.getString("weight_sensor_health"),
                rs.getString("infrared_value"),
                rs.getString("infrared_sensor_health"),
                rs.getString("smoke_state"),
                rs.getString("smoke_sensor_health"),
                rs.getString("safety_status"),
                nullableBoolean(rs, "business_enabled"),
                rs.getString("fullness_mode"),
                nullableInstant(rs, "last_observed_at"),
                rs.getLong("lock_version"));
    }

    private boolean occupied(long assetId) {
        Integer count = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_device_occupancy
                        WHERE asset_id = ?
                        """,
                Integer.class,
                assetId);
        return count != null && count > 0;
    }

    private Optional<ConfigurationVersionRow> findConfigurationVersion(
            AuthorizedScope scope,
            long deploymentId,
            long versionNo) {
        return jdbc.query("""
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
                               LOWER(HEX(config.content_sha256))
                                   AS content_sha256,
                               LOWER(HEX(config.mcu_payload_sha256))
                                   AS mcu_payload_sha256,
                               config.publication_source,
                               staff.display_name AS publisher_name,
                               config.published_at,
                               app.application_uid,
                               app.status AS app_status,
                               app.lock_version AS app_version
                        FROM dev_config_version config
                        LEFT JOIN iam_staff_account staff
                          ON staff.tenant_id = config.tenant_id
                         AND staff.id =
                             config.published_by_staff_account_id
                        JOIN dev_config_application app
                          ON app.config_version_id = config.id
                        WHERE config.tenant_id = ?
                          AND config.organization_id = ?
                          AND config.deployment_id = ?
                          AND config.version_no = ?
                        """,
                (rs, ignored) -> configurationVersionRow(rs),
                scope.tenantId(),
                scope.organizationId(),
                deploymentId,
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
            AuthorizedScope scope,
            long deploymentId,
            long configurationId) {
        return jdbc.query("""
                        SELECT p.port_no, snapshot.display_name,
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
                        JOIN dev_port p
                          ON p.tenant_id = snapshot.tenant_id
                         AND p.organization_id =
                             snapshot.organization_id
                         AND p.deployment_id = snapshot.deployment_id
                         AND p.id = snapshot.port_id
                        WHERE snapshot.tenant_id = ?
                          AND snapshot.organization_id = ?
                          AND snapshot.deployment_id = ?
                          AND snapshot.config_version_id = ?
                        ORDER BY p.port_no
                        """,
                (rs, ignored) -> new ConfigurationPortSnapshot(
                        rs.getInt("port_no"),
                        rs.getString("display_name"),
                        rs.getBoolean("business_enabled"),
                        decimalString(
                                rs.getBigDecimal(
                                        "unit_price_yuan_per_kg"),
                                4),
                        rs.getString("fullness_mode"),
                        decimalString(
                                BigDecimal.valueOf(
                                        rs.getLong(
                                                "configured_full_weight_g"),
                                        3),
                                3),
                        rs.getLong("delivery_settle_delay_ms"),
                        rs.getLong("fullness_settle_wait_ms"),
                        rs.getLong("fullness_confirmation_wait_ms"),
                        rs.getLong("door_auto_close_timeout_ms"),
                        rs.getString("fullness_sensor_kind"),
                        rs.getLong("fullness_distance_threshold_mm"),
                        rs.getInt("fullness_sample_count"),
                        rs.getInt(
                                "fullness_min_valid_sample_count"),
                        rs.getLong("fullness_echo_timeout_us"),
                        rs.getLong("weight_stable_window_ms"),
                        rs.getLong("weight_maximum_fluctuation_g"),
                        rs.getInt("weight_required_sample_count"),
                        rs.getLong("weight_measurement_timeout_ms"),
                        rs.getLong("weight_minimum_g"),
                        rs.getLong("weight_maximum_g"),
                        rs.getLong("calibration_version"),
                        rs.getLong("infrared_sample_timeout_ms"),
                        rs.getLong(
                                "delivery_door_operation_timeout_ms")),
                scope.tenantId(),
                scope.organizationId(),
                deploymentId,
                configurationId);
    }

    private Optional<ApplicationRow> findApplication(
            AuthorizedScope scope,
            long deploymentId,
            UUID applicationUid,
            boolean forUpdate) {
        if (forUpdate) {
            jdbc.query("""
                            SELECT id
                            FROM dev_config_application
                            WHERE tenant_id = ?
                              AND organization_id = ?
                              AND deployment_id = ?
                              AND application_uid = ?
                            FOR UPDATE
                            """,
                    (rs, ignored) -> rs.getLong("id"),
                    scope.tenantId(),
                    scope.organizationId(),
                    deploymentId,
                    applicationUid.toString());
        }
        return jdbc.query("""
                        SELECT app.id, app.application_uid,
                               app.status, app.reported_version_no,
                               LOWER(HEX(app.reported_content_sha256))
                                   AS reported_content_sha256,
                               LOWER(HEX(app.reported_mcu_payload_sha256))
                                   AS reported_mcu_payload_sha256,
                               app.edge_persisted_at, app.mcu_synced_at,
                               app.applied_at, app.last_failure_at,
                               app.last_failure_code, app.lock_version,
                               config.version_no,
                               LOWER(HEX(config.content_sha256))
                                   AS content_sha256,
                               LOWER(HEX(config.mcu_payload_sha256))
                                   AS mcu_payload_sha256,
                               (
                                   SELECT MAX(latest.version_no)
                                   FROM dev_config_version latest
                                   WHERE latest.tenant_id = app.tenant_id
                                     AND latest.organization_id =
                                         app.organization_id
                                     AND latest.deployment_id =
                                         app.deployment_id
                               ) AS latest_version_no
                        FROM dev_config_application app
                        JOIN dev_config_version config
                          ON config.tenant_id = app.tenant_id
                         AND config.organization_id = app.organization_id
                         AND config.deployment_id = app.deployment_id
                         AND config.id = app.config_version_id
                        WHERE app.tenant_id = ?
                          AND app.organization_id = ?
                          AND app.deployment_id = ?
                          AND app.application_uid = ?
                        """,
                (rs, ignored) -> new ApplicationRow(
                        rs.getLong("id"),
                        UUID.fromString(
                                rs.getString("application_uid")),
                        rs.getLong("version_no"),
                        rs.getLong("latest_version_no"),
                        rs.getString("content_sha256"),
                        rs.getString("mcu_payload_sha256"),
                        rs.getString("status"),
                        rs.getLong("lock_version"),
                        nullableLong(rs, "reported_version_no"),
                        rs.getString("reported_content_sha256"),
                        rs.getString(
                                "reported_mcu_payload_sha256"),
                        nullableInstant(rs, "edge_persisted_at"),
                        nullableInstant(rs, "mcu_synced_at"),
                        nullableInstant(rs, "applied_at"),
                        rs.getString("last_failure_code"),
                        nullableInstant(rs, "last_failure_at")),
                scope.tenantId(),
                scope.organizationId(),
                deploymentId,
                applicationUid.toString()).stream().findFirst();
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

    private static String publisherName(
            ConfigurationVersionRow row) {
        if (row.publisherName() != null) {
            return row.publisherName();
        }
        return "SYSTEM";
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

    private static String normalizedDecimal(String value) {
        if (value == null) {
            return null;
        }
        return new BigDecimal(value).stripTrailingZeros().toPlainString();
    }

    private static String applicationCollectionUrl(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String deploymentCode) {
        String prefix = platformPath
                ? "/api/v1/web/platform/tenants/" + tenantCode
                + "/organizations/" + organizationCode
                : "/api/v1/web/organizations/" + organizationCode;
        return prefix + "/device-deployments/" + deploymentCode
                + "/configuration-applications";
    }

    private String fingerprint(
            UUID principalUid,
            String actionCode,
            String targetStableKey,
            Object request) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            digest.update(principalUid.toString()
                    .getBytes(StandardCharsets.UTF_8));
            digest.update((byte) 0);
            digest.update(actionCode.getBytes(StandardCharsets.UTF_8));
            digest.update((byte) 0);
            digest.update(targetStableKey.getBytes(StandardCharsets.UTF_8));
            digest.update((byte) 0);
            digest.update(objectMapper.writeValueAsBytes(request));
            return HexFormat.of().formatHex(digest.digest());
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException(
                    "SHA-256 is unavailable", exception);
        }
    }

    private JsonNode readJson(String json) {
        try {
            return objectMapper.readTree(json);
        } catch (Exception exception) {
            throw idempotencyConflict();
        }
    }

    private String writeJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "device JSON cannot be encoded", exception);
        }
    }

    private long insertAndReturnKey(String sql, Object... parameters) {
        KeyHolder keyHolder = new GeneratedKeyHolder();
        PreparedStatementCreator creator = connection -> {
            PreparedStatement statement = connection.prepareStatement(
                    sql, Statement.RETURN_GENERATED_KEYS);
            for (int index = 0; index < parameters.length; index++) {
                statement.setObject(index + 1, parameters[index]);
            }
            return statement;
        };
        int inserted = jdbc.update(creator, keyHolder);
        requireSingleRow(inserted, "insert device row");
        Number key = keyHolder.getKey();
        if (key == null) {
            throw invariant();
        }
        return key.longValue();
    }

    private LocalDateTime databaseNow() {
        LocalDateTime result = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        if (result == null) {
            throw invariant();
        }
        return result;
    }

    private static void addContains(
            StringBuilder predicate,
            List<Object> parameters,
            String column,
            String value) {
        String normalized = blankToNull(value);
        if (normalized != null) {
            predicate.append(" AND ").append(column).append(" LIKE ?");
            parameters.add("%" + normalized + "%");
        }
    }

    private static void requireEnabledScope(AuthorizedScope scope) {
        if (!scope.tenantEnabled() || !scope.organizationEnabled()) {
            throw unprocessable(
                    "DEVICE.TARGET_SCOPE_DISABLED",
                    "目标租户或机构未启用");
        }
    }

    private static void requireSingleRow(int updated, String action) {
        if (updated != 1) {
            throw new IllegalStateException(
                    action + " affected " + updated + " rows");
        }
    }

    private static int page(int value) {
        return value <= 0 ? 1 : value;
    }

    private static int pageSize(int value) {
        return value <= 0
                ? DEFAULT_PAGE_SIZE
                : Math.min(value, MAX_PAGE_SIZE);
    }

    private static String normalizeHardwareSn(String value) {
        String result = requiredTrimmed(value, 64, "hardwareSn");
        if (!result.matches("[A-Za-z0-9._:-]{1,64}")) {
            throw invalidRequest();
        }
        return result;
    }

    private static String normalizeDeploymentCode(String value) {
        String result = requiredTrimmed(
                value, 64, "deploymentCode");
        if (!result.matches("Dp_[A-Za-z0-9_-]{6,61}")) {
            throw notFound();
        }
        return result;
    }

    private static String requiredTrimmed(
            String value,
            int maximumLength,
            String field) {
        if (value == null || value.isBlank()) {
            throw invalidRequest();
        }
        String result = value.trim();
        if (result.length() > maximumLength) {
            throw invalidRequest();
        }
        return result;
    }

    private static String optionalTrimmed(
            String value,
            int maximumLength,
            String field) {
        if (value == null || value.isBlank()) {
            return null;
        }
        return requiredTrimmed(value, maximumLength, field);
    }

    private static String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value.trim();
    }

    private static String upperOrNull(String value) {
        String normalized = blankToNull(value);
        return normalized == null
                ? null
                : normalized.toUpperCase(Locale.ROOT);
    }

    private static BigDecimal nullableDecimal(String value) {
        return value == null ? null : new BigDecimal(value);
    }

    private static String decimalString(
            BigDecimal value,
            int scale) {
        return value == null
                ? null
                : value.setScale(scale).toPlainString();
    }

    private static Long nullableLong(ResultSet rs, String column)
            throws SQLException {
        long value = rs.getLong(column);
        return rs.wasNull() ? null : value;
    }

    private static Integer nullableInteger(
            ResultSet rs,
            String column) throws SQLException {
        int value = rs.getInt(column);
        return rs.wasNull() ? null : value;
    }

    private static Boolean nullableBoolean(
            ResultSet rs,
            String column) throws SQLException {
        boolean value = rs.getBoolean(column);
        return rs.wasNull() ? null : value;
    }

    private static Instant instant(ResultSet rs, String column)
            throws SQLException {
        return rs.getObject(column, LocalDateTime.class)
                .toInstant(ZoneOffset.UTC);
    }

    private static Instant nullableInstant(
            ResultSet rs,
            String column) throws SQLException {
        LocalDateTime value = rs.getObject(column, LocalDateTime.class);
        return value == null ? null : value.toInstant(ZoneOffset.UTC);
    }

    private static byte[] sha256(byte[] value) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(value);
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException(
                    "SHA-256 is unavailable", exception);
        }
    }

    private static Map<String, Object> assetAuditSnapshot(
            DeviceAssetView asset) {
        return Map.of(
                "hardwareSn", asset.hardwareSn(),
                "lifecycleStatus", asset.lifecycleStatus(),
                "version", asset.version());
    }

    private static Map<String, Object> deploymentAuditSnapshot(
            DeploymentView deployment) {
        return Map.of(
                "deploymentCode", deployment.deploymentCode(),
                "lifecycleStatus", deployment.lifecycleStatus(),
                "businessEnabled", deployment.businessEnabled(),
                "version", deployment.version(),
                "assetVersion", deployment.asset().version());
    }

    private static void validateOperationUid(UUID value) {
        if (value == null || value.version() != 4 || value.variant() != 2) {
            throw new TargetApiException(
                    400,
                    "COMMON.INVALID_IDEMPOTENCY_KEY",
                    "Idempotency-Key 必须是 UUIDv4");
        }
    }

    private static TargetApiException invalidRequest() {
        return new TargetApiException(
                400,
                "COMMON.INVALID_REQUEST",
                "请求字段不符合接口契约");
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404,
                "COMMON.RESOURCE_NOT_FOUND",
                "指定设备资源不存在于当前可见范围");
    }

    private static TargetApiException assetNotFound() {
        return new TargetApiException(
                404,
                "DEVICE.ASSET_NOT_FOUND",
                "未找到指定设备资产");
    }

    private static TargetApiException applicationNotFound() {
        return new TargetApiException(
                404,
                "DEVICE.CONFIGURATION_APPLICATION_NOT_FOUND",
                "未找到指定配置应用");
    }

    private static TargetApiException versionConflict(long currentVersion) {
        return new TargetApiException(
                409,
                "COMMON.VERSION_CONFLICT",
                "资源版本已经变化",
                false,
                Map.of("currentVersion", currentVersion));
    }

    private static TargetApiException conflict(
            String code,
            String detail) {
        return new TargetApiException(409, code, detail);
    }

    private static TargetApiException unprocessable(
            String code,
            String detail) {
        return new TargetApiException(422, code, detail);
    }

    private static TargetApiException idempotencyConflict() {
        return new TargetApiException(
                409,
                "COMMON.IDEMPOTENCY_KEY_REUSED",
                "Idempotency-Key 已用于不同操作");
    }

    private static IllegalStateException invariant() {
        return new IllegalStateException(
                "target device persistence invariant violated");
    }

    private enum DeploymentMutation {
        ACTIVATE("device.deployment.activate"),
        DEACTIVATE("device.deployment.deactivate"),
        ENABLE_BUSINESS("device.business-switch.enable"),
        DISABLE_BUSINESS("device.business-switch.disable");

        private final String actionCode;

        DeploymentMutation(String actionCode) {
            this.actionCode = actionCode;
        }

        String actionCode() {
            return actionCode;
        }
    }

    private record AuthorizedScope(
            boolean platformActor,
            UUID principalUid,
            UUID sessionUid,
            String actorDisplayName,
            String tenantCode,
            String organizationCode,
            boolean tenantEnabled,
            boolean organizationEnabled,
            Long tenantId,
            Long organizationId,
            Long platformAdminId,
            Long staffAccountId) {
    }

    private record AssetRow(
            long id,
            String hardwareSn,
            String modelCode,
            int expectedPortCount,
            String lifecycleStatus,
            long version) {
    }

    private record DeploymentRow(
            long id,
            long assetId,
            String publicCode,
            String hardwareSn,
            String lifecycleStatus,
            boolean businessEnabled,
            int portCount,
            Long latestConfigurationId,
            Long latestConfigurationVersion,
            Long appliedConfigurationVersion,
            String configurationApplicationStatus,
            long version,
            DeploymentView view) {
    }

    private record RuntimeRow(
            String edgeConnectionStatus,
            String mcuLinkStatus,
            String safetyStatus,
            String aggregateWeightHealth,
            String cameraHealth,
            String localStorageHealth,
            String clockSyncHealth,
            String edgeSoftwareVersion,
            String mcuFirmwareVersion,
            String uartState,
            Integer uartProtocolMajor,
            Integer uartProtocolMinor,
            String capabilityBitmapHex,
            Long appliedConfigurationVersion,
            Long orangePiReportedConfigurationVersion,
            byte[] orangePiReportedContentSha256,
            byte[] orangePiReportedMcuPayloadSha256,
            Instant lastHeartbeatAt,
            Instant lastDeviceEventAt,
            long version) {
    }

    private record PortRuntimeRow(
            int portNo,
            String deliveryDoorState,
            String deliveryDoorActuatorHealth,
            String deliveryDoorContactState,
            String cleanLockPowerState,
            String cleanSolenoidHealth,
            String weightSensorHealth,
            String infraredValue,
            String infraredSensorHealth,
            String smokeState,
            String smokeSensorHealth,
            String safetyStatus,
            Boolean businessEnabled,
            String fullnessMode,
            Instant lastObservedAt,
            long version) {
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
            Map<String, ?> before,
            Map<String, ?> after,
            String reason) {

        private CommandResult {
            before = before == null ? Map.of() : Map.copyOf(before);
            after = after == null ? Map.of() : Map.copyOf(after);
        }
    }
}
