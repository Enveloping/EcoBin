package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.web.v1.DeviceLifecycleModels.AcceptanceReadinessView;
import org.enveloping.ecobin.device.web.v1.DeviceLifecycleModels.AcceptanceRequest;
import org.enveloping.ecobin.device.web.v1.DeviceLifecycleModels.AcceptanceView;
import org.enveloping.ecobin.device.web.v1.DeviceLifecycleModels.ConfigurationAcceptanceEvidence;
import org.enveloping.ecobin.device.web.v1.DeviceLifecycleModels.CreateTenantAllocationRequest;
import org.enveloping.ecobin.device.web.v1.DeviceLifecycleModels.CredentialRotationConfirmationRequest;
import org.enveloping.ecobin.device.web.v1.DeviceLifecycleModels.CredentialRotationConfirmationView;
import org.enveloping.ecobin.device.web.v1.DeviceLifecycleModels.MaintenanceClearanceRequest;
import org.enveloping.ecobin.device.web.v1.DeviceLifecycleModels.PortAcceptanceEvidence;
import org.enveloping.ecobin.device.web.v1.DeviceLifecycleModels.ReclaimTenantAllocationRequest;
import org.enveloping.ecobin.device.web.v1.DeviceLifecycleModels.ReturnDeploymentToTenantPoolRequest;
import org.enveloping.ecobin.device.web.v1.DeviceLifecycleModels.RuntimeAcceptanceEvidence;
import org.enveloping.ecobin.device.web.v1.DeviceLifecycleModels.TenantAllocationView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.PageData;
import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.audit.SuccessfulAudit;
import org.enveloping.ecobin.framework.web.TargetWebAuditRequestContext;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.DeviceScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.query.DeviceScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedDeviceScope;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Arrays;
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
public class DeviceLifecycleApplication {

    private static final int DEFAULT_PAGE_SIZE = 20;
    private static final int MAX_PAGE_SIZE = 200;

    private final JdbcTemplate jdbc;
    private final DeviceScopeAuthorizationPort authorizationPort;
    private final AuditPort auditPort;
    private final ObjectMapper objectMapper;

    public DeviceLifecycleApplication(
            JdbcTemplate jdbc,
            DeviceScopeAuthorizationPort authorizationPort,
            AuditPort auditPort,
            ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.authorizationPort = authorizationPort;
        this.auditPort = auditPort;
        this.objectMapper = objectMapper;
    }

    @Transactional(readOnly = true)
    public PageData<TenantAllocationView> allocations(
            boolean platformPath,
            String tenantCode,
            int requestedPage,
            int requestedPageSize,
            String status,
            String hardwareSn) {
        Scope scope = authorize(
                platformPath,
                platformPath ? blankToNull(tenantCode) : null,
                null,
                "device.read");
        int page = Math.max(1, requestedPage);
        int pageSize = requestedPageSize <= 0
                ? DEFAULT_PAGE_SIZE
                : Math.min(requestedPageSize, MAX_PAGE_SIZE);
        String normalizedStatus = upperOrNull(status);
        if (normalizedStatus != null
                && !Set.of("ACTIVE", "ENDED").contains(normalizedStatus)) {
            throw invalid("设备租户分配状态无效");
        }
        String normalizedHardware = blankToNull(hardwareSn);
        StringBuilder predicate = new StringBuilder(" WHERE 1 = 1");
        List<Object> parameters = new ArrayList<>();
        if (scope.tenantId() != null) {
            predicate.append(" AND allocation.tenant_id = ?");
            parameters.add(scope.tenantId());
        }
        if (normalizedStatus != null) {
            predicate.append(" AND allocation.status = ?");
            parameters.add(normalizedStatus);
        }
        if (normalizedHardware != null) {
            predicate.append(" AND asset.hardware_sn LIKE ? ESCAPE '\\\\'");
            parameters.add("%" + escapeLike(normalizedHardware) + "%");
        }
        long total = jdbc.queryForObject(
                "SELECT COUNT(*) " + allocationFrom() + predicate,
                Long.class,
                parameters.toArray());
        parameters.add(pageSize);
        parameters.add((long) (page - 1) * pageSize);
        List<TenantAllocationView> items = jdbc.query(
                allocationSelect() + allocationFrom() + predicate
                        + " ORDER BY allocation.allocated_at DESC, allocation.id DESC"
                        + " LIMIT ? OFFSET ?",
                (rs, ignored) -> allocationView(rs),
                parameters.toArray());
        return new PageData<>(items, page, pageSize, total);
    }

    @Transactional(readOnly = true)
    public TenantAllocationView allocation(
            boolean platformPath,
            UUID allocationUid) {
        Scope scope = authorize(
                platformPath, null, null, "device.read");
        return findAllocation(scope, allocationUid, false).view();
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public TenantAllocationView allocate(
            UUID operationUid,
            String tenantCode,
            CreateTenantAllocationRequest request) {
        Scope scope = authorize(
                true, tenantCode, null, "device.manage");
        requireEnabledTenant(scope);
        if (request == null || request.expectedAssetVersion() == null) {
            throw invalid("设备分配请求不完整");
        }
        String hardwareSn = required(request.hardwareSn(), "硬件序列号不能为空");
        CreateTenantAllocationRequest normalized =
                new CreateTenantAllocationRequest(
                        hardwareSn,
                        request.expectedAssetVersion(),
                        blankToNull(request.reason()));
        return command(
                operationUid,
                scope,
                "device.tenant-allocation.create",
                "DEVICE_TENANT_ALLOCATION",
                scope.tenantCode() + "|" + hardwareSn,
                normalized,
                TenantAllocationView.class,
                () -> createAllocation(operationUid, scope, normalized));
    }

    private CommandResult<TenantAllocationView> createAllocation(
            UUID operationUid,
            Scope scope,
            CreateTenantAllocationRequest request) {
        Asset asset = lockAsset(request.hardwareSn());
        if (!"IN_STOCK".equals(asset.lifecycleStatus())) {
            throw conflict(
                    "DEVICE.ASSET_NOT_IN_STOCK",
                    "只有库存设备可以分配给租户");
        }
        requireVersion(asset.version(), request.expectedAssetVersion());
        if (count("SELECT COUNT(*) FROM dev_asset_active_tenant_allocation WHERE asset_id = ?",
                asset.id()) > 0
                || count("SELECT COUNT(*) FROM dev_asset_active_deployment WHERE asset_id = ?",
                asset.id()) > 0) {
            throw conflict(
                    "DEVICE.ASSET_ALREADY_ALLOCATED",
                    "设备已经存在当前租户分配或机构部署");
        }
        PreviousAllocation previous = jdbc.query("""
                        SELECT tenant_id, ended_at
                        FROM dev_asset_tenant_allocation
                        WHERE asset_id = ?
                        ORDER BY allocated_at DESC, id DESC
                        LIMIT 1
                        """,
                (rs, ignored) -> new PreviousAllocation(
                        rs.getLong("tenant_id"),
                        nullableInstant(rs, "ended_at")),
                asset.id()).stream().findFirst().orElse(null);
        if (previous != null && previous.tenantId() != scope.tenantId()) {
            if (previous.endedAt() == null
                    || count("""
                            SELECT COUNT(*)
                            FROM dev_onenet_credential_rotation_confirmation
                            WHERE asset_id = ? AND confirmed_at > ?
                            """,
                    asset.id(),
                    LocalDateTime.ofInstant(
                            previous.endedAt(), ZoneOffset.UTC)) == 0) {
                throw unprocessable(
                        "DEVICE.CREDENTIAL_ROTATION_REQUIRED",
                        "设备跨租户重新分配前必须先确认已经更换 OneNet 设备密钥",
                        List.of("ONENET_CREDENTIAL_ROTATION_REQUIRED"));
            }
        }
        LocalDateTime now = databaseNow();
        UUID allocationUid = operationUid;
        jdbc.update("""
                        INSERT INTO dev_asset_tenant_allocation (
                            allocation_uid, tenant_id, asset_id,
                            allocation_source, status,
                            allocated_by_platform_admin_id, allocated_at,
                            ended_by_platform_admin_id, ended_at,
                            end_mode, end_reason, legacy_deployment_id,
                            lock_version, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, 'PLATFORM_ASSIGNMENT', 'ACTIVE',
                            ?, ?, NULL, NULL, NULL, NULL, NULL, 0, ?, ?
                        )
                        """,
                allocationUid.toString(),
                scope.tenantId(),
                asset.id(),
                scope.platformAdminId(),
                now, now, now);
        long allocationId = jdbc.queryForObject(
                "SELECT id FROM dev_asset_tenant_allocation WHERE allocation_uid = ?",
                Long.class,
                allocationUid.toString());
        jdbc.update("""
                        INSERT INTO dev_asset_active_tenant_allocation (
                            asset_id, tenant_id, allocation_id, acquired_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                asset.id(), scope.tenantId(), allocationId, now);
        int updated = jdbc.update("""
                        UPDATE dev_device_asset
                        SET lifecycle_status = 'ALLOCATED',
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND lifecycle_status = 'IN_STOCK'
                          AND lock_version = ?
                        """,
                now, asset.id(), asset.version());
        requireSingle(updated, "allocate device asset");
        TenantAllocationView view = findAllocation(
                scope, allocationUid, false).view();
        return new CommandResult<>(
                view,
                Map.of(
                        "assetLifecycleStatus", "IN_STOCK",
                        "assetVersion", asset.version()),
                view,
                request.reason());
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public TenantAllocationView returnToTenantPool(
            UUID operationUid,
            String organizationCode,
            String deploymentCode,
            ReturnDeploymentToTenantPoolRequest request) {
        Scope scope = authorize(
                false, null, organizationCode, "device.allocation.manage");
        requireEnabledScope(scope);
        if (request == null) {
            throw invalid("设备回收请求不能为空");
        }
        String normalizedDeployment = required(
                deploymentCode, "部署编码不能为空");
        return command(
                operationUid,
                scope,
                "device.deployment.return-to-tenant-pool",
                "DEVICE_DEPLOYMENT",
                normalizedDeployment,
                request,
                TenantAllocationView.class,
                () -> endDeploymentToTenantPool(
                        scope, normalizedDeployment, request));
    }

    private CommandResult<TenantAllocationView> endDeploymentToTenantPool(
            Scope scope,
            String deploymentCode,
            ReturnDeploymentToTenantPoolRequest request) {
        DeploymentClosure deployment = lockDeployment(
                scope.tenantId(), scope.organizationId(), deploymentCode);
        requireVersion(
                deployment.version(), request.expectedDeploymentVersion());
        Allocation allocation = lockAllocation(deployment.allocationId());
        requireVersion(allocation.version(), request.expectedAllocationVersion());
        List<String> blockers = closureBlockers(deployment);
        if (deployment.businessEnabled()) {
            blockers = append(blockers, "BUSINESS_STILL_ENABLED");
        }
        if (!blockers.isEmpty()) {
            throw unprocessable(
                    "DEVICE.DEPLOYMENT_NOT_RETURNABLE",
                    "设备仍有经营、作业或未上报事实，不能结束机构部署",
                    blockers);
        }
        LocalDateTime now = databaseNow();
        endDeployment(deployment, "TENANT_INTERNAL_RETURN", request.reason(), now);
        int assetUpdated = jdbc.update("""
                        UPDATE dev_device_asset
                        SET lifecycle_status = 'ALLOCATED',
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND lifecycle_status IN ('IN_USE', 'ALLOCATED')
                        """,
                now, deployment.assetId());
        requireSingle(assetUpdated, "return asset to tenant pool");
        TenantAllocationView view = findAllocation(
                scope, allocation.allocationUid(), false).view();
        return new CommandResult<>(
                view,
                Map.of("deploymentStatus", deployment.lifecycleStatus()),
                view,
                request.reason());
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public TenantAllocationView reclaim(
            UUID operationUid,
            UUID allocationUid,
            ReclaimTenantAllocationRequest request) {
        Scope scope = authorize(true, null, null, "device.manage");
        if (request == null) {
            throw invalid("平台收回请求不能为空");
        }
        String mode = upperOrNull(request.mode());
        if (!Set.of("NORMAL", "EXCEPTIONAL").contains(mode)) {
            throw invalid("平台收回模式必须是 NORMAL 或 EXCEPTIONAL");
        }
        ReclaimTenantAllocationRequest normalized =
                new ReclaimTenantAllocationRequest(
                        request.expectedAllocationVersion(),
                        request.expectedAssetVersion(),
                        mode,
                        request.physicalPossessionConfirmed(),
                        required(request.reason(), "平台收回原因不能为空"));
        return command(
                operationUid,
                scope,
                "device.tenant-allocation.reclaim",
                "DEVICE_TENANT_ALLOCATION",
                allocationUid.toString(),
                normalized,
                TenantAllocationView.class,
                () -> reclaimAllocation(
                        operationUid, scope, allocationUid, normalized));
    }

    private CommandResult<TenantAllocationView> reclaimAllocation(
            UUID operationUid,
            Scope actorScope,
            UUID allocationUid,
            ReclaimTenantAllocationRequest request) {
        Allocation allocation = lockAllocation(allocationUid);
        if (!"ACTIVE".equals(allocation.status())) {
            throw conflict(
                    "DEVICE.ALLOCATION_ALREADY_ENDED",
                    "设备租户分配已经结束");
        }
        requireVersion(
                allocation.version(), request.expectedAllocationVersion());
        Asset asset = lockAsset(allocation.hardwareSn());
        requireVersion(asset.version(), request.expectedAssetVersion());
        DeploymentClosure deployment = activeDeployment(allocation.assetId());
        List<String> blockers = deployment == null
                ? new ArrayList<>()
                : new ArrayList<>(closureBlockers(deployment));
        if (deployment != null && deployment.businessEnabled()) {
            blockers.add("BUSINESS_STILL_ENABLED");
        }
        if (!Boolean.TRUE.equals(request.physicalPossessionConfirmed())) {
            blockers.add("PHYSICAL_POSSESSION_UNCONFIRMED");
        }
        if ("NORMAL".equals(request.mode()) && !blockers.isEmpty()) {
            throw unprocessable(
                    "DEVICE.ALLOCATION_NOT_NORMALLY_RECLAIMABLE",
                    "设备不满足正常收回条件；只能处理阻断项或执行异常隔离收回",
                    blockers);
        }
        LocalDateTime now = databaseNow();
        if (deployment != null) {
            endDeployment(
                    deployment,
                    "PLATFORM_" + request.mode() + "_RECLAIM",
                    request.reason(),
                    now);
        }
        jdbc.update("""
                        DELETE FROM dev_asset_active_tenant_allocation
                        WHERE allocation_id = ? AND asset_id = ?
                        """,
                allocation.id(), allocation.assetId());
        int ended = jdbc.update("""
                        UPDATE dev_asset_tenant_allocation
                        SET status = 'ENDED',
                            ended_by_platform_admin_id = ?,
                            ended_at = ?, end_mode = ?, end_reason = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ? AND status = 'ACTIVE'
                          AND lock_version = ?
                        """,
                actorScope.platformAdminId(),
                now,
                request.mode(),
                request.reason(),
                now,
                allocation.id(),
                allocation.version());
        requireSingle(ended, "end tenant allocation");
        jdbc.update("""
                        INSERT INTO dev_asset_reclaim_record (
                            reclaim_uid, allocation_id, tenant_id, asset_id,
                            deployment_id, reclaim_mode,
                            physical_possession_confirmed,
                            blocker_snapshot_json, reclaim_reason,
                            reclaimed_by_platform_admin_id,
                            reclaimed_at, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, CAST(? AS JSON), ?, ?, ?, ?)
                        """,
                operationUid.toString(),
                allocation.id(),
                allocation.tenantId(),
                allocation.assetId(),
                deployment == null ? null : deployment.id(),
                request.mode(),
                Boolean.TRUE.equals(request.physicalPossessionConfirmed()),
                writeJson(Map.of("blockers", blockers)),
                request.reason(),
                actorScope.platformAdminId(),
                now, now);
        String nextStatus = "NORMAL".equals(request.mode())
                ? "IN_STOCK" : "MAINTENANCE";
        int assetUpdated = jdbc.update("""
                        UPDATE dev_device_asset
                        SET lifecycle_status = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ? AND lock_version = ?
                        """,
                nextStatus, now, asset.id(), asset.version());
        requireSingle(assetUpdated, "reclaim device asset");
        Scope allocationScope = actorScope.withTenant(
                allocation.tenantId(), allocation.tenantCode());
        TenantAllocationView view = findAllocation(
                allocationScope, allocationUid, false).view();
        return new CommandResult<>(
                view,
                Map.of(
                        "allocationStatus", "ACTIVE",
                        "assetLifecycleStatus", asset.lifecycleStatus()),
                view,
                request.reason());
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public CredentialRotationConfirmationView confirmCredentialRotation(
            UUID operationUid,
            String hardwareSn,
            CredentialRotationConfirmationRequest request) {
        Scope scope = authorize(true, null, null, "device.manage");
        if (request == null) {
            throw invalid("OneNet 密钥更换确认请求不能为空");
        }
        String normalizedHardware = required(
                hardwareSn, "硬件序列号不能为空");
        return command(
                operationUid,
                scope,
                "device.onenet-credential-rotation.confirm",
                "DEVICE_ASSET",
                normalizedHardware,
                request,
                CredentialRotationConfirmationView.class,
                () -> confirmRotation(
                        operationUid, scope, normalizedHardware, request));
    }

    private CommandResult<CredentialRotationConfirmationView> confirmRotation(
            UUID operationUid,
            Scope scope,
            String hardwareSn,
            CredentialRotationConfirmationRequest request) {
        Asset asset = lockAsset(hardwareSn);
        requireVersion(asset.version(), request.expectedAssetVersion());
        String reason = required(request.reason(), "密钥更换确认原因不能为空");
        LocalDateTime now = databaseNow();
        jdbc.update("""
                        INSERT INTO dev_onenet_credential_rotation_confirmation (
                            confirmation_uid, asset_id,
                            confirmed_by_platform_admin_id,
                            confirmation_reason, confirmed_at, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                operationUid.toString(),
                asset.id(),
                scope.platformAdminId(),
                reason,
                now, now);
        int updated = jdbc.update("""
                        UPDATE dev_device_asset
                        SET lock_version = lock_version + 1, updated_at = ?
                        WHERE id = ? AND lock_version = ?
                        """,
                now, asset.id(), asset.version());
        requireSingle(updated, "record OneNet credential rotation");
        CredentialRotationConfirmationView view =
                new CredentialRotationConfirmationView(
                        operationUid,
                        asset.hardwareSn(),
                        instant(now),
                        reason);
        return new CommandResult<>(view, null, view, reason);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public TenantAllocationView clearMaintenance(
            UUID operationUid,
            String hardwareSn,
            MaintenanceClearanceRequest request) {
        Scope scope = authorize(true, null, null, "device.manage");
        if (request == null) {
            throw invalid("维修隔离解除请求不能为空");
        }
        String normalizedHardware = required(
                hardwareSn, "硬件序列号不能为空");
        return command(
                operationUid,
                scope,
                "device.maintenance.clear",
                "DEVICE_ASSET",
                normalizedHardware,
                request,
                TenantAllocationView.class,
                () -> clearMaintenance(
                        operationUid, scope, normalizedHardware, request));
    }

    private CommandResult<TenantAllocationView> clearMaintenance(
            UUID operationUid,
            Scope scope,
            String hardwareSn,
            MaintenanceClearanceRequest request) {
        Asset asset = lockAsset(hardwareSn);
        requireVersion(asset.version(), request.expectedAssetVersion());
        if (!"MAINTENANCE".equals(asset.lifecycleStatus())) {
            throw conflict(
                    "DEVICE.ASSET_NOT_IN_MAINTENANCE",
                    "只有异常收回并处于维修隔离状态的设备才能解除隔离");
        }
        if (!Boolean.TRUE.equals(request.physicalPossessionConfirmed())
                || !Boolean.TRUE.equals(request.inspectionConfirmed())) {
            throw unprocessable(
                    "DEVICE.MAINTENANCE_CLEARANCE_CONFIRMATION_REQUIRED",
                    "解除维修隔离必须确认实物已收回且检查通过",
                    List.of(
                            "PHYSICAL_POSSESSION_UNCONFIRMED",
                            "INSPECTION_UNCONFIRMED"));
        }
        if (count("SELECT COUNT(*) FROM dev_asset_active_tenant_allocation WHERE asset_id = ?",
                asset.id()) > 0
                || count("SELECT COUNT(*) FROM dev_asset_active_deployment WHERE asset_id = ?",
                asset.id()) > 0) {
            throw conflict(
                    "DEVICE.ASSET_STILL_ASSIGNED",
                    "设备仍存在当前租户分配或部署，不能解除隔离");
        }
        String reason = required(request.reason(), "解除维修隔离原因不能为空");
        LocalDateTime now = databaseNow();
        jdbc.update("""
                        INSERT INTO dev_asset_maintenance_clearance (
                            clearance_uid, asset_id,
                            physical_possession_confirmed,
                            inspection_confirmed, clearance_reason,
                            cleared_by_platform_admin_id,
                            cleared_at, created_at
                        ) VALUES (?, ?, 1, 1, ?, ?, ?, ?)
                        """,
                operationUid.toString(), asset.id(), reason,
                scope.platformAdminId(), now, now);
        int updated = jdbc.update("""
                        UPDATE dev_device_asset
                        SET lifecycle_status = 'IN_STOCK',
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ? AND lifecycle_status = 'MAINTENANCE'
                          AND lock_version = ?
                        """,
                now, asset.id(), asset.version());
        requireSingle(updated, "clear device maintenance isolation");
        TenantAllocationView response = latestAllocationForAsset(asset.id());
        return new CommandResult<>(response, null, response, reason);
    }

    @Transactional(readOnly = true)
    public AcceptanceReadinessView acceptanceReadiness(
            String tenantCode,
            String organizationCode,
            String deploymentCode) {
        Scope scope = authorize(
                true, tenantCode, organizationCode, "device.read");
        return readiness(scope, required(deploymentCode, "部署编码不能为空"), false)
                .view();
    }

    @Transactional(readOnly = true)
    public List<AcceptanceView> acceptances(
            String tenantCode,
            String organizationCode,
            String deploymentCode) {
        Scope scope = authorize(
                true, tenantCode, organizationCode, "device.read");
        DeploymentIdentity deployment = deploymentIdentity(
                scope, required(deploymentCode, "部署编码不能为空"), false);
        return jdbc.query("""
                        SELECT acceptance.acceptance_uid,
                               acceptance.config_version_no,
                               acceptance.runtime_received_at,
                               acceptance.delivery_door_observed_normal,
                               acceptance.cameras_observed_normal,
                               acceptance.clean_door_installation_observed_normal,
                               admin.display_name,
                               acceptance.acceptance_reason,
                               acceptance.accepted_at
                        FROM dev_deployment_acceptance acceptance
                        JOIN iam_platform_admin admin
                          ON admin.id = acceptance.accepted_by_platform_admin_id
                        WHERE acceptance.tenant_id = ?
                          AND acceptance.organization_id = ?
                          AND acceptance.deployment_id = ?
                        ORDER BY acceptance.accepted_at DESC, acceptance.id DESC
                        """,
                (rs, ignored) -> new AcceptanceView(
                        UUID.fromString(rs.getString("acceptance_uid")),
                        deployment.publicCode(),
                        rs.getLong("config_version_no"),
                        instant(rs, "runtime_received_at"),
                        rs.getBoolean("delivery_door_observed_normal"),
                        rs.getBoolean("cameras_observed_normal"),
                        rs.getBoolean("clean_door_installation_observed_normal"),
                        rs.getString("display_name"),
                        rs.getString("acceptance_reason"),
                        instant(rs, "accepted_at")),
                scope.tenantId(), scope.organizationId(), deployment.id());
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public AcceptanceView accept(
            UUID operationUid,
            String tenantCode,
            String organizationCode,
            String deploymentCode,
            AcceptanceRequest request) {
        Scope scope = authorize(
                true, tenantCode, organizationCode, "device.manage");
        requireEnabledScope(scope);
        if (request == null) {
            throw invalid("设备验收请求不能为空");
        }
        String normalizedDeployment = required(
                deploymentCode, "部署编码不能为空");
        return command(
                operationUid,
                scope,
                "device.deployment.accept",
                "DEVICE_DEPLOYMENT",
                normalizedDeployment,
                request,
                AcceptanceView.class,
                () -> acceptDeployment(
                        operationUid,
                        scope,
                        normalizedDeployment,
                        request));
    }

    private CommandResult<AcceptanceView> acceptDeployment(
            UUID operationUid,
            Scope scope,
            String deploymentCode,
            AcceptanceRequest request) {
        DeploymentIdentity deployment = deploymentIdentity(
                scope, deploymentCode, true);
        requireVersion(
                deployment.version(), request.expectedDeploymentVersion());
        if (!Set.of("PENDING_INSTALL", "COMMISSIONING", "DISABLED")
                .contains(deployment.lifecycleStatus())) {
            throw conflict(
                    "DEVICE.DEPLOYMENT_NOT_ACCEPTABLE",
                    "只有调试中或技术停用的部署可以验收恢复");
        }
        if ("AUTOMATIC_TRANSFER_READINESS".equals(
                deployment.readinessMode())) {
            throw conflict(
                    "DEVICE.ACCEPTANCE_NOT_REQUIRED",
                    "同一租户内部调拨使用自动技术就绪，不需要厂家重复验收");
        }
        AcceptanceState state = readiness(scope, deploymentCode, true);
        if (!Objects.equals(
                state.configurationVersion(),
                request.expectedConfigurationVersion())) {
            throw versionConflict(
                    state.configurationVersion() == null
                            ? 0 : state.configurationVersion());
        }
        List<String> blockers = new ArrayList<>(state.view().blockers());
        if (!Boolean.TRUE.equals(request.deliveryDoorObservedNormal())) {
            blockers.add("DELIVERY_DOOR_MANUAL_CONFIRMATION_REQUIRED");
        }
        if (!Boolean.TRUE.equals(request.camerasObservedNormal())) {
            blockers.add("CAMERA_MANUAL_CONFIRMATION_REQUIRED");
        }
        if (!Boolean.TRUE.equals(
                request.cleanDoorInstallationObservedNormal())) {
            blockers.add("CLEAN_DOOR_MANUAL_CONFIRMATION_REQUIRED");
        }
        if (!blockers.isEmpty()) {
            throw unprocessable(
                    "DEVICE.DEPLOYMENT_NOT_ACCEPTABLE",
                    "设备尚未满足静态硬件验收条件",
                    blockers);
        }
        LocalDateTime now = databaseNow();
        String evidenceJson = writeJson(Map.of(
                "configuration", state.view().configuration(),
                "runtime", state.view().runtime(),
                "ports", state.view().ports(),
                "blockers", state.view().blockers()));
        jdbc.update("""
                        INSERT INTO dev_deployment_acceptance (
                            acceptance_uid, tenant_id, organization_id,
                            deployment_id, config_version_id,
                            config_version_no, runtime_edge_event_id,
                            runtime_received_at, evidence_json,
                            delivery_door_observed_normal,
                            cameras_observed_normal,
                            clean_door_installation_observed_normal,
                            accepted_by_platform_admin_id,
                            acceptance_reason, accepted_at, created_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, CAST(? AS JSON),
                            1, 1, 1, ?, ?, ?, ?
                        )
                        """,
                operationUid.toString(),
                scope.tenantId(), scope.organizationId(), deployment.id(),
                state.configurationId(), state.configurationVersion(),
                state.runtimeEventId(),
                LocalDateTime.ofInstant(
                        state.runtimeReceivedAt(), ZoneOffset.UTC),
                evidenceJson,
                scope.platformAdminId(),
                blankToNull(request.reason()),
                now, now);
        int updated = jdbc.update("""
                        UPDATE dev_device_deployment
                        SET lifecycle_status = 'ENABLED',
                            business_enabled = 0,
                            enabled_at = COALESCE(enabled_at, ?),
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ? AND lock_version = ?
                        """,
                now, now, deployment.id(), deployment.version());
        requireSingle(updated, "accept device deployment");
        jdbc.update("""
                        UPDATE dev_device_fault_event
                        SET status = 'RECOVERED',
                            recovery_source_kind = 'SYSTEM_VERIFIED',
                            recovery_method = 'INITIAL_ACCEPTANCE',
                            recovered_at = ?,
                            recovered_by_staff_account_id = NULL,
                            recovery_reason = ?,
                            lock_version = lock_version + 1
                        WHERE tenant_id = ? AND organization_id = ?
                          AND deployment_id = ?
                          AND fault_code = 'INITIAL_COMMISSIONING'
                          AND status = 'OPEN'
                        """,
                now,
                blankToNull(request.reason()) == null
                        ? "platform hardware acceptance passed"
                        : request.reason(),
                scope.tenantId(), scope.organizationId(), deployment.id());
        AcceptanceView view = new AcceptanceView(
                operationUid,
                deploymentCode,
                state.configurationVersion(),
                state.runtimeReceivedAt(),
                true, true, true,
                scope.actorDisplayName(),
                blankToNull(request.reason()),
                instant(now));
        return new CommandResult<>(
                view,
                Map.of("lifecycleStatus", deployment.lifecycleStatus()),
                view,
                request.reason());
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public void suspendTechnically(
            UUID operationUid,
            String tenantCode,
            String organizationCode,
            String deploymentCode,
            long expectedVersion,
            String reason) {
        Scope scope = authorize(
                true, tenantCode, organizationCode, "device.manage");
        command(
                operationUid,
                scope,
                "device.deployment.technical-suspend",
                "DEVICE_DEPLOYMENT",
                deploymentCode,
                Map.of(
                        "expectedVersion", expectedVersion,
                        "reason", blankToNull(reason) == null ? "" : reason),
                Boolean.class,
                () -> {
                    DeploymentIdentity deployment = deploymentIdentity(
                            scope, deploymentCode, true);
                    requireVersion(deployment.version(), expectedVersion);
                    if (!"ENABLED".equals(deployment.lifecycleStatus())) {
                        throw conflict(
                                "DEVICE.DEPLOYMENT_NOT_ENABLED",
                                "只有技术就绪的部署可以执行技术停用");
                    }
                    LocalDateTime now = databaseNow();
                    int updated = jdbc.update("""
                                    UPDATE dev_device_deployment
                                    SET lifecycle_status = 'DISABLED',
                                        business_enabled = 0,
                                        lock_version = lock_version + 1,
                                        updated_at = ?
                                    WHERE id = ? AND lock_version = ?
                                    """,
                            now, deployment.id(), deployment.version());
                    requireSingle(updated, "suspend device deployment");
                    return new CommandResult<>(
                            Boolean.TRUE,
                            Map.of("lifecycleStatus", "ENABLED"),
                            Map.of(
                                    "lifecycleStatus", "DISABLED",
                                    "businessEnabled", false),
                            reason);
                });
    }

    public boolean automaticallyReady(String deploymentCode) {
        Integer ready = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_device_deployment deployment
                        JOIN dev_config_version config
                          ON config.id = (
                              SELECT latest.id
                              FROM dev_config_version latest
                              WHERE latest.deployment_id = deployment.id
                              ORDER BY latest.version_no DESC LIMIT 1
                          )
                        JOIN dev_config_application application
                          ON application.config_version_id = config.id
                         AND application.status = 'APPLIED'
                        JOIN dev_deployment_runtime_state runtime
                          ON runtime.deployment_id = deployment.id
                        WHERE deployment.public_code = ?
                          AND deployment.readiness_mode =
                              'AUTOMATIC_TRANSFER_READINESS'
                          AND deployment.lifecycle_status = 'COMMISSIONING'
                          AND runtime.edge_connection_status = 'ONLINE'
                          AND runtime.uart_state = 'READY'
                          AND runtime.mcu_link_status = 'ONLINE'
                          AND runtime.safety_status = 'SAFE'
                          AND runtime.aggregate_weight_health = 'OK'
                          AND runtime.camera_health IN ('OK', 'UNKNOWN')
                          AND runtime.local_storage_health = 'OK'
                          AND runtime.clock_sync_health = 'OK'
                          AND runtime.pending_reliable_event_count = 0
                          AND runtime.trusted_runtime_received_at IS NOT NULL
                          AND runtime.orange_pi_reported_config_version_no =
                              config.version_no
                          AND runtime.orange_pi_reported_config_content_sha256 =
                              config.content_sha256
                          AND runtime.orange_pi_reported_config_mcu_payload_sha256 =
                              config.mcu_payload_sha256
                          AND NOT EXISTS (
                              SELECT 1
                              FROM dev_port port
                              LEFT JOIN dev_port_runtime_state port_runtime
                                ON port_runtime.port_id = port.id
                              WHERE port.deployment_id = deployment.id
                                AND (
                                    port_runtime.port_id IS NULL
                                    OR port_runtime.trusted_runtime_edge_event_id IS NULL
                                    OR port_runtime.trusted_runtime_edge_event_id <>
                                        runtime.trusted_runtime_edge_event_id
                                    OR port_runtime.clean_lock_power_state <>
                                        'DEENERGIZED'
                                    OR port_runtime.clean_solenoid_health <> 'OK'
                                    OR port_runtime.weight_sensor_health <> 'OK'
                                    OR port_runtime.fullness_sensor_kind IS NULL
                                    OR port_runtime.fullness_sensor_kind <>
                                        'DIGITAL_INFRARED'
                                    OR port_runtime.infrared_sensor_health <> 'OK'
                                    OR port_runtime.infrared_value IS NULL
                                    OR port_runtime.infrared_value NOT IN (
                                        'CLEAR', 'BLOCKED'
                                    )
                                    OR port_runtime.smoke_sensor_health <> 'OK'
                                    OR port_runtime.smoke_state <> 'NORMAL'
                                    OR port_runtime.safety_status <> 'SAFE'
                                    OR port_runtime.runtime_fault_bitmap IS NULL
                                    OR port_runtime.runtime_fault_bitmap <> 0
                                )
                          )
                          AND (
                              SELECT COUNT(*) FROM dev_port port_count
                              WHERE port_count.deployment_id = deployment.id
                          ) = (
                              SELECT expected_port_count
                              FROM dev_device_asset
                              WHERE id = deployment.asset_id
                          )
                          AND NOT EXISTS (
                              SELECT 1 FROM dev_device_fault_event fault
                              WHERE fault.deployment_id = deployment.id
                                AND fault.status = 'OPEN'
                                AND fault.fault_code <> 'INITIAL_COMMISSIONING'
                                AND fault.impact_level IN (
                                    'BUSINESS_BLOCKING', 'SAFETY_BLOCKING'
                                )
                          )
                          AND NOT EXISTS (
                              SELECT 1 FROM dev_device_occupancy occupancy
                              WHERE occupancy.asset_id = deployment.asset_id
                          )
                        """,
                Integer.class,
                deploymentCode);
        return ready != null && ready == 1;
    }

    public void promoteAutomaticReadiness(String deploymentCode) {
        if (!automaticallyReady(deploymentCode)) {
            return;
        }
        LocalDateTime now = databaseNow();
        jdbc.update("""
                        UPDATE dev_device_deployment
                        SET lifecycle_status = 'ENABLED',
                            business_enabled = 0,
                            enabled_at = COALESCE(enabled_at, ?),
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE public_code = ?
                          AND readiness_mode = 'AUTOMATIC_TRANSFER_READINESS'
                          AND lifecycle_status = 'COMMISSIONING'
                        """,
                now, now, deploymentCode);
        jdbc.update("""
                        UPDATE dev_device_fault_event fault
                        JOIN dev_device_deployment deployment
                          ON deployment.id = fault.deployment_id
                        SET fault.status = 'RECOVERED',
                            fault.recovery_source_kind = 'SYSTEM_VERIFIED',
                            fault.recovery_method = 'INITIAL_ACCEPTANCE',
                            fault.recovered_at = ?,
                            fault.recovery_reason =
                                'same-tenant transfer runtime became ready',
                            fault.lock_version = fault.lock_version + 1
                        WHERE deployment.public_code = ?
                          AND fault.fault_code = 'INITIAL_COMMISSIONING'
                          AND fault.status = 'OPEN'
                        """,
                now, deploymentCode);
    }

    private AcceptanceState readiness(
            Scope scope,
            String deploymentCode,
            boolean forUpdate) {
        DeploymentIdentity deployment = deploymentIdentity(
                scope, deploymentCode, forUpdate);
        ReadinessRow row = jdbc.query("""
                        SELECT latest.id AS config_id,
                               latest.version_no AS latest_version,
                               latest.edge_heartbeat_interval_ms,
                               latest.edge_heartbeat_miss_threshold,
                               latest.content_sha256,
                               latest.mcu_payload_sha256,
                               application.status AS application_status,
                               runtime.edge_connection_status,
                               runtime.mcu_link_status,
                               runtime.uart_state,
                               runtime.aggregate_weight_health,
                               runtime.camera_health,
                               runtime.local_storage_health,
                               runtime.clock_sync_health,
                               runtime.edge_software_version,
                               runtime.mcu_firmware_version,
                               runtime.pending_reliable_event_count,
                               runtime.trusted_runtime_edge_event_id,
                               runtime.trusted_runtime_received_at,
                               runtime.orange_pi_reported_config_version_no,
                               runtime.orange_pi_reported_config_content_sha256,
                               runtime.orange_pi_reported_config_mcu_payload_sha256,
                               runtime.safety_status,
                               TIMESTAMPDIFF(
                                   MICROSECOND,
                                   runtime.trusted_runtime_received_at,
                                   UTC_TIMESTAMP(3)
                               ) AS runtime_age_us
                        FROM dev_device_deployment deployment
                        LEFT JOIN dev_config_version latest
                          ON latest.id = (
                              SELECT candidate.id
                              FROM dev_config_version candidate
                              WHERE candidate.deployment_id = deployment.id
                              ORDER BY candidate.version_no DESC LIMIT 1
                          )
                        LEFT JOIN dev_config_application application
                          ON application.config_version_id = latest.id
                        LEFT JOIN dev_deployment_runtime_state runtime
                          ON runtime.deployment_id = deployment.id
                        WHERE deployment.id = ?
                        """,
                (rs, ignored) -> readinessRow(rs),
                deployment.id()).stream().findFirst()
                .orElseThrow(DeviceLifecycleApplication::notFound);
        List<PortAcceptanceEvidence> ports = jdbc.query("""
                        SELECT port.port_no,
                               runtime.clean_lock_power_state,
                               runtime.clean_solenoid_health,
                               runtime.weight_sensor_health,
                               runtime.infrared_value,
                               runtime.infrared_sensor_health,
                               runtime.fullness_sensor_kind,
                               runtime.smoke_state,
                               runtime.smoke_sensor_health,
                               runtime.safety_status,
                               runtime.runtime_fault_bitmap,
                               runtime.trusted_runtime_edge_event_id,
                               runtime.last_observed_at,
                               runtime.last_delivery_door_output_status
                        FROM dev_port port
                        JOIN dev_port_runtime_state runtime
                          ON runtime.port_id = port.id
                        WHERE port.deployment_id = ?
                        ORDER BY port.port_no
                        """,
                (rs, ignored) -> new PortAcceptanceEvidence(
                        rs.getInt("port_no"),
                        rs.getString("clean_lock_power_state"),
                        rs.getString("clean_solenoid_health"),
                        rs.getString("weight_sensor_health"),
                        rs.getString("infrared_value"),
                        rs.getString("infrared_sensor_health"),
                        rs.getString("fullness_sensor_kind"),
                        rs.getString("smoke_state"),
                        rs.getString("smoke_sensor_health"),
                        rs.getString("safety_status"),
                        nullableLong(rs, "runtime_fault_bitmap"),
                        nullableLong(rs, "trusted_runtime_edge_event_id"),
                        nullableInstant(rs, "last_observed_at")),
                deployment.id());
        LinkedHashSet<String> blockers = new LinkedHashSet<>();
        boolean exactConfiguration = row.configId() != null
                && "APPLIED".equals(row.applicationStatus())
                && Objects.equals(
                        row.latestVersion(), row.reportedVersion())
                && Arrays.equals(
                        row.contentSha256(), row.reportedContentSha256())
                && Arrays.equals(
                        row.mcuPayloadSha256(), row.reportedMcuPayloadSha256());
        if (!exactConfiguration) {
            blockers.add("CONFIGURATION_NOT_APPLIED");
        }
        if (row.runtimeEventId() == null
                || row.runtimeReceivedAt() == null) {
            blockers.add("TRUSTED_RUNTIME_MISSING");
        }
        if (!"ONLINE".equals(row.edgeConnectionStatus())) {
            blockers.add("EDGE_OFFLINE");
        }
        if (!"ONLINE".equals(row.mcuLinkStatus())
                || !"READY".equals(row.uartState())) {
            blockers.add("MCU_COMMUNICATION_NOT_READY");
        }
        if (!"OK".equals(row.aggregateWeightHealth())) {
            blockers.add("WEIGHT_SENSOR_UNHEALTHY");
        }
        if (!Set.of("OK", "UNKNOWN").contains(row.cameraHealth())) {
            blockers.add("CAMERA_UNHEALTHY");
        }
        if (!"OK".equals(row.localStorageHealth())) {
            blockers.add("LOCAL_STORAGE_UNHEALTHY");
        }
        if (!"OK".equals(row.clockSyncHealth())) {
            blockers.add("CLOCK_NOT_SYNCHRONIZED");
        }
        if (!"SAFE".equals(row.safetyStatus())) {
            blockers.add("SAFETY_LOCKED");
        }
        if (row.pendingReliableEventCount() == null
                || row.pendingReliableEventCount() != 0) {
            blockers.add("PENDING_RELIABLE_EVENTS");
        }
        if (ports.size() != deployment.expectedPortCount()) {
            blockers.add("PORT_RUNTIME_INCOMPLETE");
        }
        for (PortAcceptanceEvidence port : ports) {
            if (!Objects.equals(
                    port.runtimeEdgeEventId(), row.runtimeEventId())) {
                blockers.add("PORT_RUNTIME_NOT_FROM_LATEST_SNAPSHOT");
            }
            if (!"DEENERGIZED".equals(port.cleanLockPowerState())
                    || !"OK".equals(port.cleanSolenoidHealth())) {
                blockers.add("CLEAN_LOCK_UNHEALTHY");
            }
            if (!"OK".equals(port.weightSensorHealth())) {
                blockers.add("WEIGHT_SENSOR_UNHEALTHY");
            }
            if (!"DIGITAL_INFRARED".equals(port.fullnessSensorKind())
                    || !"OK".equals(port.infraredSensorHealth())
                    || !Set.of("CLEAR", "BLOCKED")
                    .contains(port.infraredValue())) {
                blockers.add("DIGITAL_INFRARED_UNHEALTHY");
            }
            if (!"OK".equals(port.smokeSensorHealth())
                    || !"NORMAL".equals(port.smokeState())) {
                blockers.add("SMOKE_SENSOR_UNHEALTHY");
            }
            if (!"SAFE".equals(port.safetyStatus())
                    || port.runtimeFaultBitmap() == null
                    || port.runtimeFaultBitmap() != 0) {
                blockers.add("PORT_SAFETY_FAULT");
            }
        }
        if (count("""
                        SELECT COUNT(*) FROM dev_device_fault_event
                        WHERE deployment_id = ? AND status = 'OPEN'
                          AND fault_code <> 'INITIAL_COMMISSIONING'
                          AND impact_level IN (
                              'BUSINESS_BLOCKING', 'SAFETY_BLOCKING'
                          )
                        """,
                deployment.id()) > 0) {
            blockers.add("SERIOUS_FAULT_OPEN");
        }
        if (count("SELECT COUNT(*) FROM dev_device_occupancy WHERE asset_id = ?",
                deployment.assetId()) > 0) {
            blockers.add("DEVICE_BUSY");
        }
        ConfigurationAcceptanceEvidence configuration =
                new ConfigurationAcceptanceEvidence(
                        row.latestVersion(),
                        row.reportedVersion(),
                        row.applicationStatus(),
                        exactConfiguration);
        RuntimeAcceptanceEvidence runtime = new RuntimeAcceptanceEvidence(
                row.edgeConnectionStatus(),
                row.mcuLinkStatus(),
                row.uartState(),
                row.aggregateWeightHealth(),
                row.cameraHealth(),
                row.localStorageHealth(),
                row.clockSyncHealth(),
                row.edgeSoftwareVersion(),
                row.mcuFirmwareVersion(),
                row.pendingReliableEventCount(),
                row.runtimeReceivedAt());
        AcceptanceReadinessView view = new AcceptanceReadinessView(
                deployment.publicCode(),
                deployment.readinessMode(),
                blockers.isEmpty(),
                List.copyOf(blockers),
                configuration,
                runtime,
                ports);
        return new AcceptanceState(
                view,
                row.configId(),
                row.latestVersion(),
                row.runtimeEventId(),
                row.runtimeReceivedAt());
    }

    static long heartbeatWindowMicros(
            long heartbeatIntervalMs,
            long heartbeatMissThreshold) {
        if (heartbeatIntervalMs <= 0 || heartbeatMissThreshold <= 0) {
            throw new IllegalArgumentException(
                    "heartbeat values must be positive");
        }
        long maximumWindowMicros = 86_400_000_000L;
        long maximumIntervalBeforeSaturation =
                maximumWindowMicros / 1_000L / heartbeatMissThreshold;
        if (heartbeatIntervalMs > maximumIntervalBeforeSaturation) {
            return maximumWindowMicros;
        }
        return heartbeatIntervalMs * heartbeatMissThreshold * 1_000L;
    }

    private List<String> closureBlockers(DeploymentClosure deployment) {
        LinkedHashSet<String> blockers = new LinkedHashSet<>();
        if (count("SELECT COUNT(*) FROM dev_device_occupancy WHERE asset_id = ?",
                deployment.assetId()) > 0) {
            blockers.add("DEVICE_BUSY");
        }
        RuntimeClosure runtime = jdbc.query("""
                        SELECT edge_connection_status,
                               pending_reliable_event_count,
                               trusted_runtime_received_at
                        FROM dev_deployment_runtime_state
                        WHERE deployment_id = ?
                        """,
                (rs, ignored) -> new RuntimeClosure(
                        rs.getString("edge_connection_status"),
                        nullableLong(rs, "pending_reliable_event_count"),
                        nullableInstant(rs, "trusted_runtime_received_at")),
                deployment.id()).stream().findFirst().orElse(null);
        if (runtime == null
                || !"ONLINE".equals(runtime.edgeConnectionStatus())) {
            blockers.add("EDGE_OFFLINE");
        }
        if (runtime == null
                || runtime.pendingReliableEventCount() == null
                || runtime.pendingReliableEventCount() != 0) {
            blockers.add("PENDING_RELIABLE_EVENTS");
        }
        if (count("""
                        SELECT COUNT(*) FROM dev_delivery_session
                        WHERE deployment_id = ?
                          AND status IN (
                              'PREPARED',
                              'AUTHORIZATION_QUEUED',
                              'IN_PROGRESS',
                              'RESULT_PENDING_RECOVERY'
                          )
                        """,
                deployment.id()) > 0) {
            blockers.add("DELIVERY_SESSION_ACTIVE");
        }
        return List.copyOf(blockers);
    }

    private void endDeployment(
            DeploymentClosure deployment,
            String method,
            String reason,
            LocalDateTime now) {
        jdbc.update("""
                        DELETE FROM dev_asset_active_deployment
                        WHERE asset_id = ? AND deployment_id = ?
                        """,
                deployment.assetId(), deployment.id());
        int ended = jdbc.update("""
                        UPDATE dev_device_deployment
                        SET lifecycle_status = 'ENDED',
                            business_enabled = 0,
                            ended_at = ?, end_method = ?, end_reason = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ? AND lifecycle_status <> 'ENDED'
                          AND lock_version = ?
                        """,
                now, method, reason, now,
                deployment.id(), deployment.version());
        requireSingle(ended, "end device deployment");
    }

    private AllocationRow findAllocation(
            Scope scope,
            UUID allocationUid,
            boolean forUpdate) {
        String tenantPredicate = scope.tenantId() == null
                ? "" : " AND allocation.tenant_id = ?";
        List<Object> parameters = new ArrayList<>();
        parameters.add(allocationUid.toString());
        if (scope.tenantId() != null) {
            parameters.add(scope.tenantId());
        }
        String lock = forUpdate ? " FOR UPDATE" : "";
        return jdbc.query(
                allocationSelect() + allocationFrom()
                        + " WHERE allocation.allocation_uid = ?"
                        + tenantPredicate + lock,
                (rs, ignored) -> new AllocationRow(
                        rs.getLong("allocation_id"),
                        UUID.fromString(rs.getString("allocation_uid")),
                        allocationView(rs)),
                parameters.toArray()).stream().findFirst()
                .orElseThrow(DeviceLifecycleApplication::notFound);
    }

    private static String allocationSelect() {
        return """
                SELECT allocation.id AS allocation_id,
                       allocation.allocation_uid,
                       allocation.status AS allocation_status,
                       allocation.allocation_source,
                       allocation.lock_version AS allocation_version,
                       allocation.allocated_at, allocation.ended_at,
                       allocation.end_mode, allocation.end_reason,
                       tenant.tenant_code,
                       asset.hardware_sn, asset.model_name,
                       asset.expected_port_count,
                       asset.lifecycle_status AS asset_status,
                       asset.lock_version AS asset_version,
                       deployment.public_code AS deployment_code,
                       organization.organization_code,
                       CASE
                           WHEN previous_tenant.tenant_id IS NULL
                                OR previous_tenant.tenant_id = allocation.tenant_id
                               THEN 0
                           WHEN EXISTS (
                               SELECT 1
                               FROM dev_onenet_credential_rotation_confirmation rotation
                               WHERE rotation.asset_id = allocation.asset_id
                                 AND rotation.confirmed_at >
                                     previous_tenant.ended_at
                           ) THEN 0
                           ELSE 1
                       END AS credential_rotation_required
                """;
    }

    private static String allocationFrom() {
        return """
                FROM dev_asset_tenant_allocation allocation
                JOIN iam_tenant tenant ON tenant.id = allocation.tenant_id
                JOIN dev_device_asset asset ON asset.id = allocation.asset_id
                LEFT JOIN dev_asset_active_deployment active_deployment
                  ON active_deployment.asset_id = allocation.asset_id
                 AND allocation.status = 'ACTIVE'
                LEFT JOIN dev_device_deployment deployment
                  ON deployment.id = active_deployment.deployment_id
                LEFT JOIN iam_organization organization
                  ON organization.id = active_deployment.organization_id
                 AND organization.tenant_id = allocation.tenant_id
                LEFT JOIN dev_asset_tenant_allocation previous_tenant
                  ON previous_tenant.id = (
                      SELECT previous_lookup.id
                      FROM dev_asset_tenant_allocation previous_lookup
                      WHERE previous_lookup.asset_id = allocation.asset_id
                        AND previous_lookup.id < allocation.id
                      ORDER BY previous_lookup.id DESC LIMIT 1
                  )
                """;
    }

    private TenantAllocationView allocationView(ResultSet rs)
            throws SQLException {
        return new TenantAllocationView(
                UUID.fromString(rs.getString("allocation_uid")),
                rs.getString("tenant_code"),
                rs.getString("hardware_sn"),
                rs.getString("model_name"),
                rs.getInt("expected_port_count"),
                rs.getString("allocation_status"),
                rs.getString("asset_status"),
                rs.getString("allocation_source"),
                rs.getString("deployment_code"),
                rs.getString("organization_code"),
                rs.getBoolean("credential_rotation_required"),
                rs.getLong("allocation_version"),
                rs.getLong("asset_version"),
                instant(rs, "allocated_at"),
                nullableInstant(rs, "ended_at"),
                rs.getString("end_mode"),
                rs.getString("end_reason"));
    }

    private TenantAllocationView latestAllocationForAsset(long assetId) {
        return jdbc.query(
                allocationSelect() + allocationFrom()
                        + " WHERE allocation.asset_id = ?"
                        + " ORDER BY allocation.id DESC LIMIT 1",
                (rs, ignored) -> allocationView(rs),
                assetId).stream().findFirst()
                .orElseThrow(DeviceLifecycleApplication::notFound);
    }

    private Allocation lockAllocation(UUID allocationUid) {
        return jdbc.query("""
                        SELECT allocation.id, allocation.allocation_uid,
                               allocation.tenant_id, tenant.tenant_code,
                               allocation.asset_id, asset.hardware_sn,
                               allocation.status, allocation.lock_version
                        FROM dev_asset_tenant_allocation allocation
                        JOIN iam_tenant tenant ON tenant.id = allocation.tenant_id
                        JOIN dev_device_asset asset ON asset.id = allocation.asset_id
                        WHERE allocation.allocation_uid = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> allocation(rs),
                allocationUid.toString()).stream().findFirst()
                .orElseThrow(DeviceLifecycleApplication::notFound);
    }

    private Allocation lockAllocation(long allocationId) {
        return jdbc.query("""
                        SELECT allocation.id, allocation.allocation_uid,
                               allocation.tenant_id, tenant.tenant_code,
                               allocation.asset_id, asset.hardware_sn,
                               allocation.status, allocation.lock_version
                        FROM dev_asset_tenant_allocation allocation
                        JOIN iam_tenant tenant ON tenant.id = allocation.tenant_id
                        JOIN dev_device_asset asset ON asset.id = allocation.asset_id
                        WHERE allocation.id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> allocation(rs),
                allocationId).stream().findFirst()
                .orElseThrow(DeviceLifecycleApplication::notFound);
    }

    private static Allocation allocation(ResultSet rs) throws SQLException {
        return new Allocation(
                rs.getLong("id"),
                UUID.fromString(rs.getString("allocation_uid")),
                rs.getLong("tenant_id"),
                rs.getString("tenant_code"),
                rs.getLong("asset_id"),
                rs.getString("hardware_sn"),
                rs.getString("status"),
                rs.getLong("lock_version"));
    }

    private Asset lockAsset(String hardwareSn) {
        return jdbc.query("""
                        SELECT id, hardware_sn, lifecycle_status, lock_version
                        FROM dev_device_asset
                        WHERE hardware_sn = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new Asset(
                        rs.getLong("id"),
                        rs.getString("hardware_sn"),
                        rs.getString("lifecycle_status"),
                        rs.getLong("lock_version")),
                hardwareSn).stream().findFirst()
                .orElseThrow(DeviceLifecycleApplication::notFound);
    }

    private DeploymentClosure lockDeployment(
            long tenantId,
            long organizationId,
            String deploymentCode) {
        return jdbc.query("""
                        SELECT id, tenant_allocation_id, asset_id,
                               lifecycle_status, business_enabled, lock_version
                        FROM dev_device_deployment
                        WHERE tenant_id = ? AND organization_id = ?
                          AND public_code = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> closure(rs),
                tenantId, organizationId, deploymentCode).stream().findFirst()
                .orElseThrow(DeviceLifecycleApplication::notFound);
    }

    private DeploymentClosure activeDeployment(long assetId) {
        return jdbc.query("""
                        SELECT deployment.id,
                               deployment.tenant_allocation_id,
                               deployment.asset_id,
                               deployment.lifecycle_status,
                               deployment.business_enabled,
                               deployment.lock_version
                        FROM dev_asset_active_deployment active
                        JOIN dev_device_deployment deployment
                          ON deployment.id = active.deployment_id
                        WHERE active.asset_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> closure(rs),
                assetId).stream().findFirst().orElse(null);
    }

    private static DeploymentClosure closure(ResultSet rs)
            throws SQLException {
        return new DeploymentClosure(
                rs.getLong("id"),
                rs.getLong("tenant_allocation_id"),
                rs.getLong("asset_id"),
                rs.getString("lifecycle_status"),
                rs.getBoolean("business_enabled"),
                rs.getLong("lock_version"));
    }

    private DeploymentIdentity deploymentIdentity(
            Scope scope,
            String deploymentCode,
            boolean forUpdate) {
        String lock = forUpdate ? " FOR UPDATE" : "";
        return jdbc.query("""
                        SELECT deployment.id, deployment.asset_id,
                               deployment.public_code,
                               deployment.lifecycle_status,
                               deployment.readiness_mode,
                               deployment.lock_version,
                               asset.expected_port_count
                        FROM dev_device_deployment deployment
                        JOIN dev_device_asset asset
                          ON asset.id = deployment.asset_id
                        WHERE deployment.tenant_id = ?
                          AND deployment.organization_id = ?
                          AND deployment.public_code = ?
                        """ + lock,
                (rs, ignored) -> new DeploymentIdentity(
                        rs.getLong("id"),
                        rs.getLong("asset_id"),
                        rs.getString("public_code"),
                        rs.getString("lifecycle_status"),
                        rs.getString("readiness_mode"),
                        rs.getLong("lock_version"),
                        rs.getInt("expected_port_count")),
                scope.tenantId(), scope.organizationId(), deploymentCode)
                .stream().findFirst()
                .orElseThrow(DeviceLifecycleApplication::notFound);
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
                (tenantKey, organizationKey, platformKey, staffKey) -> {
                    keys[0] = tenantKey;
                    keys[1] = organizationKey;
                    keys[2] = platformKey;
                    keys[3] = staffKey;
                });
        return new Scope(
                authorization.platformActor(),
                authorization.principalUid(),
                authorization.sessionUid(),
                authorization.actorDisplayName(),
                authorization.tenantCode(),
                authorization.organizationCode(),
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
        String fingerprint = fingerprint(
                scope.principalUid(), actionCode, targetStableKey, request);
        Optional<SuccessfulAudit> previous =
                auditPort.findSuccessful(operationUid);
        if (previous.isPresent()) {
            SuccessfulAudit audit = previous.get();
            JsonNode summary = readJson(audit.safeChangeSummaryJson());
            boolean sameActor = scope.platformActor()
                    ? audit.actorKind() == AuditActorKind.PLATFORM_ADMIN
                    && Objects.equals(
                    audit.platformAdminId(), scope.platformAdminId())
                    : audit.actorKind() == AuditActorKind.STAFF_ACCOUNT
                    && Objects.equals(
                    audit.staffAccountId(), scope.staffAccountId());
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
        AuditScopeKind auditScope = scope.organizationId() != null
                ? AuditScopeKind.ORGANIZATION
                : scope.tenantId() != null
                ? AuditScopeKind.TENANT : AuditScopeKind.PLATFORM;
        auditPort.append(new AuditEntry(
                UUID.randomUUID(),
                UUID.randomUUID(),
                operationUid,
                auditScope,
                scope.tenantId(), scope.organizationId(),
                scope.platformActor()
                        ? AuditActorKind.PLATFORM_ADMIN
                        : AuditActorKind.STAFF_ACCOUNT,
                scope.platformAdminId(), scope.staffAccountId(),
                null, null,
                scope.actorDisplayName(),
                actionCode, targetType, targetStableKey,
                "WEB", "SUCCEEDED", scope.sessionUid(),
                blankToNull(result.reason()),
                writeJson(summary), Instant.now()));
        return result.response();
    }

    private String fingerprint(
            UUID principalUid,
            String actionCode,
            String target,
            Object request) {
        return HexFormat.of().formatHex(sha256(
                (principalUid + "|" + actionCode + "|" + target + "|"
                        + writeJson(request)).getBytes(StandardCharsets.UTF_8)));
    }

    private String writeJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (Exception exception) {
            throw new IllegalStateException("cannot serialize audit data", exception);
        }
    }

    private JsonNode readJson(String json) {
        try {
            return objectMapper.readTree(json);
        } catch (Exception exception) {
            throw idempotencyConflict();
        }
    }

    private LocalDateTime databaseNow() {
        return jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
    }

    private int count(String sql, Object... parameters) {
        Integer value = jdbc.queryForObject(sql, Integer.class, parameters);
        return value == null ? 0 : value;
    }

    private static void requireEnabledTenant(Scope scope) {
        if (scope.tenantId() == null || !scope.tenantEnabled()) {
            throw conflict(
                    "DEVICE.TENANT_DISABLED",
                    "目标租户未启用，不能分配设备");
        }
    }

    private static void requireEnabledScope(Scope scope) {
        requireEnabledTenant(scope);
        if (scope.organizationId() == null || !scope.organizationEnabled()) {
            throw conflict(
                    "DEVICE.ORGANIZATION_DISABLED",
                    "目标机构未启用，不能操作设备");
        }
    }

    private static void requireVersion(long actual, Long expected) {
        if (expected == null || actual != expected) {
            throw versionConflict(actual);
        }
    }

    private static void requireSingle(int count, String action) {
        if (count != 1) {
            throw new IllegalStateException(
                    "expected one row while attempting to " + action);
        }
    }

    private static String required(String value, String message) {
        String normalized = blankToNull(value);
        if (normalized == null) {
            throw invalid(message);
        }
        return normalized;
    }

    private static String upperOrNull(String value) {
        String normalized = blankToNull(value);
        return normalized == null
                ? null : normalized.toUpperCase(Locale.ROOT);
    }

    private static String blankToNull(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        return value.trim();
    }

    private static String escapeLike(String value) {
        return value.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_");
    }

    private static List<String> append(
            List<String> values, String value) {
        List<String> result = new ArrayList<>(values);
        result.add(value);
        return result;
    }

    private static byte[] sha256(byte[] value) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(value);
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException(exception);
        }
    }

    private static void requireUuidV4(UUID value) {
        if (value == null || value.version() != 4) {
            throw invalid("Idempotency-Key 必须是 UUID v4");
        }
    }

    private static Instant instant(LocalDateTime value) {
        return value.toInstant(ZoneOffset.UTC);
    }

    private static Instant instant(ResultSet rs, String column)
            throws SQLException {
        return rs.getObject(column, LocalDateTime.class)
                .toInstant(ZoneOffset.UTC);
    }

    private static Instant nullableInstant(ResultSet rs, String column)
            throws SQLException {
        LocalDateTime value = rs.getObject(column, LocalDateTime.class);
        return value == null ? null : value.toInstant(ZoneOffset.UTC);
    }

    private static Long nullableLong(ResultSet rs, String column)
            throws SQLException {
        long value = rs.getLong(column);
        return rs.wasNull() ? null : value;
    }

    private static ReadinessRow readinessRow(ResultSet rs)
            throws SQLException {
        return new ReadinessRow(
                nullableLong(rs, "config_id"),
                nullableLong(rs, "latest_version"),
                nullableLong(rs, "edge_heartbeat_interval_ms"),
                nullableLong(rs, "edge_heartbeat_miss_threshold"),
                rs.getBytes("content_sha256"),
                rs.getBytes("mcu_payload_sha256"),
                rs.getString("application_status"),
                rs.getString("edge_connection_status"),
                rs.getString("mcu_link_status"),
                rs.getString("uart_state"),
                rs.getString("aggregate_weight_health"),
                rs.getString("camera_health"),
                rs.getString("local_storage_health"),
                rs.getString("clock_sync_health"),
                rs.getString("edge_software_version"),
                rs.getString("mcu_firmware_version"),
                nullableLong(rs, "pending_reliable_event_count"),
                nullableLong(rs, "trusted_runtime_edge_event_id"),
                nullableInstant(rs, "trusted_runtime_received_at"),
                nullableLong(rs, "orange_pi_reported_config_version_no"),
                rs.getBytes("orange_pi_reported_config_content_sha256"),
                rs.getBytes("orange_pi_reported_config_mcu_payload_sha256"),
                rs.getString("safety_status"),
                nullableLong(rs, "runtime_age_us"));
    }

    private static TargetApiException invalid(String message) {
        return new TargetApiException(
                400, "COMMON.INVALID_REQUEST", message, false, Map.of());
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404,
                "COMMON.RESOURCE_NOT_FOUND",
                "目标设备、分配或部署不存在于当前可见范围");
    }

    private static TargetApiException conflict(String code, String message) {
        return new TargetApiException(409, code, message);
    }

    private static TargetApiException versionConflict(long actual) {
        return new TargetApiException(
                409,
                "COMMON.VERSION_CONFLICT",
                "资源版本已变化，请刷新后重试",
                false,
                Map.of("actualVersion", actual));
    }

    private static TargetApiException unprocessable(
            String code, String message, List<String> blockers) {
        return new TargetApiException(
                422, code, message, false, Map.of("blockers", blockers));
    }

    private static TargetApiException idempotencyConflict() {
        return new TargetApiException(
                409,
                "COMMON.IDEMPOTENCY_CONFLICT",
                "相同 Idempotency-Key 已用于不同操作");
    }

    private record Scope(
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

        Scope withTenant(long id, String code) {
            return new Scope(
                    platformActor, principalUid, sessionUid,
                    actorDisplayName, code, null,
                    true, true, id, null,
                    platformAdminId, staffAccountId);
        }
    }

    private record Asset(
            long id,
            String hardwareSn,
            String lifecycleStatus,
            long version) {
    }

    private record PreviousAllocation(long tenantId, Instant endedAt) {
    }

    private record Allocation(
            long id,
            UUID allocationUid,
            long tenantId,
            String tenantCode,
            long assetId,
            String hardwareSn,
            String status,
            long version) {
    }

    private record AllocationRow(
            long id,
            UUID allocationUid,
            TenantAllocationView view) {
    }

    private record DeploymentClosure(
            long id,
            long allocationId,
            long assetId,
            String lifecycleStatus,
            boolean businessEnabled,
            long version) {
    }

    private record DeploymentIdentity(
            long id,
            long assetId,
            String publicCode,
            String lifecycleStatus,
            String readinessMode,
            long version,
            int expectedPortCount) {
    }

    private record RuntimeClosure(
            String edgeConnectionStatus,
            Long pendingReliableEventCount,
            Instant runtimeReceivedAt) {
    }

    private record ReadinessRow(
            Long configId,
            Long latestVersion,
            Long heartbeatIntervalMs,
            Long heartbeatMissThreshold,
            byte[] contentSha256,
            byte[] mcuPayloadSha256,
            String applicationStatus,
            String edgeConnectionStatus,
            String mcuLinkStatus,
            String uartState,
            String aggregateWeightHealth,
            String cameraHealth,
            String localStorageHealth,
            String clockSyncHealth,
            String edgeSoftwareVersion,
            String mcuFirmwareVersion,
            Long pendingReliableEventCount,
            Long runtimeEventId,
            Instant runtimeReceivedAt,
            Long reportedVersion,
            byte[] reportedContentSha256,
            byte[] reportedMcuPayloadSha256,
            String safetyStatus,
            Long runtimeAgeUs) {
    }

    private record AcceptanceState(
            AcceptanceReadinessView view,
            Long configurationId,
            Long configurationVersion,
            Long runtimeEventId,
            Instant runtimeReceivedAt) {
    }

    private record CommandResult<T>(
            T response,
            Object before,
            Object after,
            String reason) {
    }
}
