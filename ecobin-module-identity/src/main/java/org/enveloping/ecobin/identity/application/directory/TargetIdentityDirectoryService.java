package org.enveloping.ecobin.identity.application.directory;

import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.audit.SuccessfulAudit;
import org.enveloping.ecobin.identity.application.web.OrganizationAccess;
import org.enveloping.ecobin.identity.application.web.TargetWebActor;
import org.enveloping.ecobin.identity.application.web.TargetWebActorContext;
import org.enveloping.ecobin.framework.web.TargetWebAuditRequestContext;
import org.enveloping.ecobin.identity.application.web.WebAccountType;
import org.enveloping.ecobin.identity.infrastructure.persistence.v1.TargetIdentitySessionRepository;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.AccountVersionCommand;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.ActivateMembershipRequest;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.ChangeOwnPasswordRequest;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.CreateMembershipRequest;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.CreateOrganizationRequest;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.CreatePrincipalAccountRequest;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.CreateStaffAccountRequest;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.CreateTenantRequest;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.EffectiveAccessView;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.MembershipAuthorizationRequest;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.MembershipView;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.OrganizationEffectiveAccess;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.OrganizationView;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.PageData;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.PermissionDefinitionView;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.PrincipalAccountSummary;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.ProvisionedOrganizationStaffView;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.ProvisionOrganizationStaffRequest;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.ReplaceTenantPermissionsRequest;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.ResetPasswordRequest;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.SafeChange;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.StaffAccountView;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.TenantView;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.UpdateOrganizationProfileRequest;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.UpdateStaffProfileRequest;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.UpdateTenantProfileRequest;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.VersionCommand;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Comparator;
import java.util.HashMap;
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
public class TargetIdentityDirectoryService {

    private static final int DEFAULT_PAGE_SIZE = 20;
    private static final int MAX_PAGE_SIZE = 200;

    private final JdbcTemplate jdbc;
    private final PasswordEncoder passwordEncoder;
    private final TargetIdentitySessionRepository sessionRepository;
    private final AuditPort auditPort;
    private final ObjectMapper objectMapper;

    public TargetIdentityDirectoryService(
            JdbcTemplate jdbc,
            PasswordEncoder passwordEncoder,
            TargetIdentitySessionRepository sessionRepository,
            AuditPort auditPort,
            ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.passwordEncoder = passwordEncoder;
        this.sessionRepository = sessionRepository;
        this.auditPort = auditPort;
        this.objectMapper = objectMapper;
    }

    @Transactional(readOnly = true)
    public PageData<TenantView> listTenants(
            int requestedPage,
            int requestedPageSize,
            String status,
            String query) {
        requirePlatform();
        int page = page(requestedPage);
        int pageSize = pageSize(requestedPageSize);
        List<TenantView> filtered = jdbc.query("""
                        SELECT id, tenant_code, enterprise_name, status,
                               contact_name, contact_phone, contact_address,
                               lock_version, created_at, updated_at
                        FROM iam_tenant
                        ORDER BY tenant_code
                        """,
                (rs, ignored) -> tenantView(tenantRow(rs)));
        if (status != null && !status.isBlank()) {
            String normalized = status.trim().toUpperCase(Locale.ROOT);
            filtered = filtered.stream()
                    .filter(item -> item.status().equals(normalized))
                    .toList();
        }
        if (query != null && !query.isBlank()) {
            String needle = query.trim().toLowerCase(Locale.ROOT);
            filtered = filtered.stream()
                    .filter(item -> item.tenantCode().contains(needle)
                            || item.enterpriseName().toLowerCase(Locale.ROOT)
                            .contains(needle))
                    .toList();
        }
        return page(filtered, page, pageSize);
    }

    @Transactional(readOnly = true)
    public TenantView getTenant(String tenantCode) {
        TargetWebActor actor = TargetWebActorContext.required();
        TenantRow tenant = tenantByCode(normalizeCode(tenantCode), false);
        if (!actor.platform()) {
            requireSameTenant(actor, tenant.id());
            requireTenantCapability(actor, "tenant.read", "tenant.manage");
        }
        return tenantView(tenant);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public TenantView createTenant(
            UUID operationUid,
            CreateTenantRequest request) {
        TargetWebActor actor = requirePlatform();
        String code = normalizeCode(request.tenantCode());
        return command(
                operationUid,
                "identity.tenant.create",
                "tenant:" + code,
                request,
                () -> replayTenant(code),
                () -> {
                    try {
                        jdbc.update("""
                                        INSERT INTO iam_tenant (
                                            tenant_code, enterprise_name, status,
                                            contact_name, contact_phone,
                                            contact_address, lock_version,
                                            created_at, updated_at
                                        ) VALUES (
                                            ?, ?, 'DISABLED', ?, ?, ?, 0,
                                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                                        )
                                        """,
                                code,
                                request.enterpriseName().trim(),
                                blankToNull(request.contactName()),
                                blankToNull(request.contactPhone()),
                                blankToNull(request.contactAddress()));
                    } catch (DataIntegrityViolationException conflict) {
                        throw conflict(
                                "IDENTITY.TENANT_CODE_ALREADY_USED",
                                "租户编码已被使用");
                    }
                    TenantRow tenant = tenantByCode(code, false);
                    TenantView view = tenantView(tenant);
                    return result(
                            view,
                            AuditScopeKind.TENANT,
                            tenant.id(),
                            null,
                            "tenant",
                            code,
                            null,
                            view,
                            null);
                });
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public TenantView updateTenantProfile(
            UUID operationUid,
            String tenantCode,
            UpdateTenantProfileRequest request) {
        TargetWebActor actor = TargetWebActorContext.required();
        String code = normalizeCode(tenantCode);
        return command(
                operationUid,
                "identity.tenant.profile.update",
                "tenant:" + code,
                request,
                () -> replayTenant(code),
                () -> {
                    TenantRow tenant = tenantByCode(code, true);
                    if (!actor.platform()) {
                        requireSameTenant(actor, tenant.id());
                        requireTenantCapability(actor, "tenant.manage");
                    }
                    requireVersion(tenant.version(), request.expectedVersion());
                    TenantView before = tenantView(tenant);
                    jdbc.update("""
                                    UPDATE iam_tenant
                                    SET enterprise_name = ?,
                                        contact_name = ?,
                                        contact_phone = ?,
                                        contact_address = ?,
                                        lock_version = lock_version + 1,
                                        updated_at = UTC_TIMESTAMP(3)
                                    WHERE id = ?
                                    """,
                            request.enterpriseName().trim(),
                            blankToNull(request.contactName()),
                            blankToNull(request.contactPhone()),
                            blankToNull(request.contactAddress()),
                            tenant.id());
                    TenantView after = tenantView(tenantById(tenant.id(), false));
                    return result(
                            after,
                            AuditScopeKind.TENANT,
                            tenant.id(),
                            null,
                            "tenant",
                            code,
                            before,
                            after,
                            null);
                });
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public StaffAccountView createPrincipalAccount(
            UUID operationUid,
            String tenantCode,
            CreatePrincipalAccountRequest request) {
        requirePlatform();
        String code = normalizeCode(tenantCode);
        return command(
                operationUid,
                "identity.tenant.principal.create",
                "tenant:" + code,
                request,
                () -> replayPrincipal(code),
                () -> {
                    TenantRow tenant = tenantByCode(code, true);
                    requireVersion(tenant.version(), request.expectedVersion());
                    if (principalForTenant(tenant.id()) != null) {
                        throw conflict(
                                "IDENTITY.TENANT_PRINCIPAL_ALREADY_EXISTS",
                                "租户主体账号已经存在");
                    }
                    UUID staffUid = UUID.randomUUID();
                    try {
                        jdbc.update("""
                                        INSERT INTO iam_staff_account (
                                            tenant_id, staff_account_uid,
                                            account_kind, login_name,
                                            password_hash, display_name,
                                            contact_phone, enabled,
                                            failed_login_count, locked_until,
                                            auth_version, password_changed_at,
                                            lock_version, created_at, updated_at
                                        ) VALUES (
                                            ?, ?, 'TENANT_PRINCIPAL', ?, ?, ?, ?,
                                            1, 0, NULL, 0, UTC_TIMESTAMP(3), 0,
                                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                                        )
                                        """,
                                tenant.id(),
                                staffUid.toString(),
                                normalizeLogin(request.loginName()),
                                passwordEncoder.encode(request.initialPassword()),
                                request.displayName().trim(),
                                blankToNull(request.contactPhone()));
                    } catch (DataIntegrityViolationException conflict) {
                        throw conflict(
                                "IDENTITY.LOGIN_NAME_ALREADY_USED",
                                "登录名已被使用");
                    }
                    jdbc.update("""
                                    UPDATE iam_tenant
                                    SET lock_version = lock_version + 1,
                                        updated_at = UTC_TIMESTAMP(3)
                                    WHERE id = ?
                                    """, tenant.id());
                    StaffAccountView view =
                            staffView(staffByUid(tenant.id(), staffUid, false));
                    return result(
                            view,
                            AuditScopeKind.TENANT,
                            tenant.id(),
                            null,
                            "staff-account",
                            staffUid.toString(),
                            null,
                            view,
                            null);
                });
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public TenantView activateTenant(
            UUID operationUid,
            String tenantCode,
            VersionCommand request) {
        return changeTenantStatus(
                operationUid, tenantCode, request, "ENABLED");
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public TenantView deactivateTenant(
            UUID operationUid,
            String tenantCode,
            VersionCommand request) {
        return changeTenantStatus(
                operationUid, tenantCode, request, "DISABLED");
    }

    private TenantView changeTenantStatus(
            UUID operationUid,
            String tenantCode,
            VersionCommand request,
            String desiredStatus) {
        requirePlatform();
        String code = normalizeCode(tenantCode);
        return command(
                operationUid,
                desiredStatus.equals("ENABLED")
                        ? "identity.tenant.activate"
                        : "identity.tenant.deactivate",
                "tenant:" + code,
                request,
                () -> replayTenant(code),
                () -> {
                    TenantRow tenant = tenantByCode(code, true);
                    requireVersion(tenant.version(), request.expectedVersion());
                    if (tenant.status().equals(desiredStatus)) {
                        throw conflict(
                                "IDENTITY.TENANT_STATE_CONFLICT",
                                "租户已经处于目标状态");
                    }
                    if ("ENABLED".equals(desiredStatus)) {
                        StaffRow principal = principalForTenant(tenant.id());
                        if (principal == null || !principal.enabled()) {
                            throw unprocessable(
                                    "IDENTITY.TENANT_PRINCIPAL_REQUIRED",
                                    "启用租户前需要有效主体账号");
                        }
                    }
                    TenantView before = tenantView(tenant);
                    jdbc.update("""
                                    UPDATE iam_tenant
                                    SET status = ?,
                                        lock_version = lock_version + 1,
                                        updated_at = UTC_TIMESTAMP(3)
                                    WHERE id = ?
                                    """, desiredStatus, tenant.id());
                    if ("DISABLED".equals(desiredStatus)) {
                        sessionRepository.revokeAllTenantSessions(
                                tenant.id(),
                                "TENANT_DISABLED");
                    }
                    TenantView after = tenantView(tenantById(tenant.id(), false));
                    return result(
                            after,
                            AuditScopeKind.TENANT,
                            tenant.id(),
                            null,
                            "tenant",
                            code,
                            before,
                            after,
                            request.reason());
                });
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public StaffAccountView resetPrincipalPassword(
            UUID operationUid,
            String tenantCode,
            ResetPasswordRequest request) {
        requirePlatform();
        String code = normalizeCode(tenantCode);
        return command(
                operationUid,
                "identity.tenant.principal.password-reset",
                "tenant:" + code,
                request,
                () -> replayPrincipal(code),
                () -> {
                    TenantRow tenant = tenantByCode(code, true);
                    StaffRow principal = principalForTenant(tenant.id());
                    if (principal == null) {
                        throw notFound();
                    }
                    requireAccountVersions(principal, request.expectedVersion(),
                            request.expectedAuthVersion());
                    StaffAccountView before = staffView(principal);
                    updatePassword(principal, request.newPassword());
                    StaffAccountView after = staffView(staffById(
                            tenant.id(), principal.id(), false));
                    return result(
                            after,
                            AuditScopeKind.TENANT,
                            tenant.id(),
                            null,
                            "staff-account",
                            principal.uid().toString(),
                            before,
                            after,
                            null);
                });
    }

    @Transactional(readOnly = true)
    public PageData<OrganizationView> listOrganizations(
            String tenantCode,
            int requestedPage,
            int requestedPageSize,
            String status,
            String query) {
        TargetWebActor actor = TargetWebActorContext.required();
        TenantRow tenant = tenantForRequest(actor, tenantCode, false);
        List<OrganizationView> organizations = jdbc.query("""
                        SELECT id, tenant_id, organization_code,
                               organization_name, status, contact_phone,
                               contact_address, lock_version, created_at,
                               updated_at
                        FROM iam_organization
                        WHERE tenant_id = ?
                        ORDER BY organization_code
                        """,
                (rs, ignored) -> organizationView(organizationRow(rs)),
                tenant.id());
        if (!actor.platform()
                && !actor.hasTenantCapability("organization.read")) {
            organizations = organizations.stream()
                    .filter(item -> actor.hasOrganizationCapability(
                            item.organizationCode(), "organization.read"))
                    .toList();
        } else if (!actor.platform()) {
            requireTenantCapability(actor, "organization.read");
        }
        if (status != null && !status.isBlank()) {
            String normalized = status.trim().toUpperCase(Locale.ROOT);
            organizations = organizations.stream()
                    .filter(item -> item.status().equals(normalized))
                    .toList();
        }
        if (query != null && !query.isBlank()) {
            String needle = query.trim().toLowerCase(Locale.ROOT);
            organizations = organizations.stream()
                    .filter(item -> item.organizationCode().contains(needle)
                            || item.organizationName().toLowerCase(Locale.ROOT)
                            .contains(needle))
                    .toList();
        }
        return page(
                organizations,
                page(requestedPage),
                pageSize(requestedPageSize));
    }

    @Transactional(readOnly = true)
    public OrganizationView getOrganization(
            String tenantCode,
            String organizationCode) {
        TargetWebActor actor = TargetWebActorContext.required();
        TenantRow tenant = tenantForRequest(actor, tenantCode, false);
        OrganizationRow organization = organizationByCode(
                tenant.id(), normalizeCode(organizationCode), false);
        if (!actor.platform()
                && !actor.hasTenantCapability("organization.read")
                && !actor.hasOrganizationCapability(
                organization.code(), "organization.read")
                && !actor.hasOrganizationCapability(
                organization.code(), "organization.manage")) {
            throw notFound();
        }
        return organizationView(organization);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public OrganizationView createOrganization(
            UUID operationUid,
            String tenantCode,
            CreateOrganizationRequest request) {
        TargetWebActor actor = TargetWebActorContext.required();
        String organizationCode = normalizeCode(request.organizationCode());
        return command(
                operationUid,
                "identity.organization.create",
                "tenant:" + normalizeCode(tenantCode)
                        + "|organization:" + organizationCode,
                request,
                () -> replayOrganization(tenantCode, organizationCode),
                () -> {
                    TenantRow tenant = tenantForRequest(actor, tenantCode, true);
                    if (!actor.platform()) {
                        requireTenantCapability(actor, "organization.manage");
                    }
                    try {
                        jdbc.update("""
                                        INSERT INTO iam_organization (
                                            tenant_id, organization_code,
                                            organization_name, status,
                                            contact_phone, contact_address,
                                            lock_version, created_at, updated_at
                                        ) VALUES (
                                            ?, ?, ?, 'DISABLED', ?, ?, 0,
                                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                                        )
                                        """,
                                tenant.id(),
                                organizationCode,
                                request.organizationName().trim(),
                                blankToNull(request.contactPhone()),
                                blankToNull(request.contactAddress()));
                    } catch (DataIntegrityViolationException conflict) {
                        throw conflict(
                                "IDENTITY.ORGANIZATION_CODE_ALREADY_USED",
                                "机构编码已被使用");
                    }
                    OrganizationRow organization = organizationByCode(
                            tenant.id(), organizationCode, false);
                    OrganizationView view = organizationView(organization);
                    return result(
                            view,
                            AuditScopeKind.ORGANIZATION,
                            tenant.id(),
                            organization.id(),
                            "organization",
                            organizationCode,
                            null,
                            view,
                            null);
                });
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public OrganizationView updateOrganizationProfile(
            UUID operationUid,
            String tenantCode,
            String organizationCode,
            UpdateOrganizationProfileRequest request) {
        TargetWebActor actor = TargetWebActorContext.required();
        String code = normalizeCode(organizationCode);
        return command(
                operationUid,
                "identity.organization.profile.update",
                "tenant:" + normalizeCode(tenantCode)
                        + "|organization:" + code,
                request,
                () -> replayOrganization(tenantCode, code),
                () -> {
                    TenantRow tenant = tenantForRequest(actor, tenantCode, true);
                    OrganizationRow organization = organizationByCode(
                            tenant.id(), code, true);
                    requireOrganizationCapability(
                            actor, organization, "organization.manage");
                    requireVersion(
                            organization.version(), request.expectedVersion());
                    OrganizationView before = organizationView(organization);
                    jdbc.update("""
                                    UPDATE iam_organization
                                    SET organization_name = ?,
                                        contact_phone = ?,
                                        contact_address = ?,
                                        lock_version = lock_version + 1,
                                        updated_at = UTC_TIMESTAMP(3)
                                    WHERE id = ?
                                      AND tenant_id = ?
                                    """,
                            request.organizationName().trim(),
                            blankToNull(request.contactPhone()),
                            blankToNull(request.contactAddress()),
                            organization.id(),
                            tenant.id());
                    OrganizationView after = organizationView(
                            organizationById(tenant.id(), organization.id(), false));
                    return result(
                            after,
                            AuditScopeKind.ORGANIZATION,
                            tenant.id(),
                            organization.id(),
                            "organization",
                            code,
                            before,
                            after,
                            null);
                });
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public OrganizationView activateOrganization(
            UUID operationUid,
            String tenantCode,
            String organizationCode,
            VersionCommand request) {
        return changeOrganizationStatus(
                operationUid,
                tenantCode,
                organizationCode,
                request,
                "ENABLED");
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public OrganizationView deactivateOrganization(
            UUID operationUid,
            String tenantCode,
            String organizationCode,
            VersionCommand request) {
        return changeOrganizationStatus(
                operationUid,
                tenantCode,
                organizationCode,
                request,
                "DISABLED");
    }

    private OrganizationView changeOrganizationStatus(
            UUID operationUid,
            String tenantCode,
            String organizationCode,
            VersionCommand request,
            String desiredStatus) {
        TargetWebActor actor = TargetWebActorContext.required();
        String code = normalizeCode(organizationCode);
        return command(
                operationUid,
                desiredStatus.equals("ENABLED")
                        ? "identity.organization.activate"
                        : "identity.organization.deactivate",
                "tenant:" + normalizeCode(tenantCode)
                        + "|organization:" + code,
                request,
                () -> replayOrganization(tenantCode, code),
                () -> {
                    TenantRow tenant = tenantForRequest(actor, tenantCode, true);
                    OrganizationRow organization = organizationByCode(
                            tenant.id(), code, true);
                    requireOrganizationCapability(
                            actor, organization, "organization.manage");
                    requireVersion(
                            organization.version(), request.expectedVersion());
                    if (organization.status().equals(desiredStatus)) {
                        throw conflict(
                                "IDENTITY.ORGANIZATION_STATE_CONFLICT",
                                "机构已经处于目标状态");
                    }
                    OrganizationView before = organizationView(organization);
                    jdbc.update("""
                                    UPDATE iam_organization
                                    SET status = ?,
                                        lock_version = lock_version + 1,
                                        updated_at = UTC_TIMESTAMP(3)
                                    WHERE id = ?
                                      AND tenant_id = ?
                                    """,
                            desiredStatus,
                            organization.id(),
                            tenant.id());
                    if ("DISABLED".equals(desiredStatus)) {
                        sessionRepository.revokeOrganizationSessions(
                                tenant.id(),
                                organization.id(),
                                "ORGANIZATION_DISABLED");
                    }
                    OrganizationView after = organizationView(
                            organizationById(tenant.id(), organization.id(), false));
                    return result(
                            after,
                            AuditScopeKind.ORGANIZATION,
                            tenant.id(),
                            organization.id(),
                            "organization",
                            code,
                            before,
                            after,
                            request.reason());
                });
    }

    @Transactional(readOnly = true)
    public PageData<StaffAccountView> listStaff(
            String tenantCode,
            int requestedPage,
            int requestedPageSize,
            String status,
            String query) {
        TargetWebActor actor = TargetWebActorContext.required();
        TenantRow tenant = tenantForRequest(actor, tenantCode, false);
        List<StaffAccountView> accounts = jdbc.query("""
                        SELECT id, tenant_id, staff_account_uid, account_kind,
                               login_name, password_hash, display_name,
                               contact_phone, enabled, auth_version,
                               lock_version, created_at, updated_at
                        FROM iam_staff_account
                        WHERE tenant_id = ?
                        ORDER BY login_name
                        """,
                (rs, ignored) -> staffView(staffRow(rs)),
                tenant.id());
        if (!actor.platform() && !actor.hasTenantCapability("staff.read")) {
            Set<Long> visibleIds = visibleStaffIds(actor, tenant.id());
            accounts = accounts.stream()
                    .filter(item -> visibleIds.contains(
                            staffByUid(tenant.id(), item.staffAccountUid(), false).id()))
                    .toList();
            if (visibleIds.isEmpty()) {
                requireAnyOrganizationCapability(actor, "staff.read");
            }
        }
        if (status != null && !status.isBlank()) {
            String normalized = status.trim().toUpperCase(Locale.ROOT);
            accounts = accounts.stream()
                    .filter(item -> item.status().equals(normalized))
                    .toList();
        }
        if (query != null && !query.isBlank()) {
            String needle = query.trim().toLowerCase(Locale.ROOT);
            accounts = accounts.stream()
                    .filter(item -> item.loginName().contains(needle)
                            || item.displayName().toLowerCase(Locale.ROOT)
                            .contains(needle))
                    .toList();
        }
        return page(accounts, page(requestedPage), pageSize(requestedPageSize));
    }

    @Transactional(readOnly = true)
    public StaffAccountView getStaff(String tenantCode, UUID staffUid) {
        TargetWebActor actor = TargetWebActorContext.required();
        TenantRow tenant = tenantForRequest(actor, tenantCode, false);
        StaffRow target = staffByUid(tenant.id(), staffUid, false);
        requireStaffVisible(actor, tenant.id(), target.id());
        return staffView(target);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public StaffAccountView createStaff(
            UUID operationUid,
            String tenantCode,
            CreateStaffAccountRequest request) {
        TargetWebActor actor = TargetWebActorContext.required();
        return command(
                operationUid,
                "identity.staff.create",
                "tenant:" + normalizeCode(tenantCode)
                        + "|login:" + normalizeLogin(request.loginName()),
                request,
                () -> replayStaffByLogin(tenantCode, request.loginName()),
                () -> {
                    TenantRow tenant = tenantForRequest(actor, tenantCode, true);
                    requireTenantCapabilityUnlessPlatform(
                            actor, "staff.manage");
                    Set<String> permissions = normalizePermissions(
                            request.permissionCodes());
                    if (!permissions.isEmpty()) {
                        requireTenantCapabilityUnlessPlatform(
                                actor, "permission.manage");
                        requireDelegableTenant(actor, permissions);
                    }
                    UUID uid = UUID.randomUUID();
                    try {
                        jdbc.update("""
                                        INSERT INTO iam_staff_account (
                                            tenant_id, staff_account_uid,
                                            account_kind, login_name,
                                            password_hash, display_name,
                                            contact_phone, enabled,
                                            failed_login_count, locked_until,
                                            auth_version, password_changed_at,
                                            lock_version, created_at, updated_at
                                        ) VALUES (
                                            ?, ?, 'STAFF', ?, ?, ?, ?, 1, 0,
                                            NULL, 0, UTC_TIMESTAMP(3), 0,
                                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                                        )
                                        """,
                                tenant.id(),
                                uid.toString(),
                                normalizeLogin(request.loginName()),
                                passwordEncoder.encode(request.initialPassword()),
                                request.displayName().trim(),
                                blankToNull(request.contactPhone()));
                    } catch (DataIntegrityViolationException conflict) {
                        throw conflict(
                                "IDENTITY.LOGIN_NAME_ALREADY_USED",
                                "登录名已被使用");
                    }
                    StaffRow staff = staffByUid(tenant.id(), uid, true);
                    replaceGrants(
                            tenant.id(),
                            staff.id(),
                            null,
                            "TENANT",
                            permissions);
                    StaffAccountView view = staffView(staffById(
                            tenant.id(), staff.id(), false));
                    return result(
                            view,
                            AuditScopeKind.TENANT,
                            tenant.id(),
                            null,
                            "staff-account",
                            uid.toString(),
                            null,
                            view,
                            null);
                });
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public ProvisionedOrganizationStaffView provisionOrganizationStaff(
            UUID operationUid,
            String tenantCode,
            String organizationCode,
            ProvisionOrganizationStaffRequest request) {
        TargetWebActor actor = TargetWebActorContext.required();
        String normalizedOrganizationCode = normalizeCode(organizationCode);
        return command(
                operationUid,
                "identity.organization.staff.provision",
                "tenant:" + normalizeCode(tenantCode)
                        + "|organization:" + normalizedOrganizationCode
                        + "|login:" + normalizeLogin(request.loginName()),
                request,
                () -> replayProvisionedStaff(
                        tenantCode,
                        normalizedOrganizationCode,
                        request.loginName()),
                () -> {
                    TenantRow tenant = tenantForRequest(actor, tenantCode, true);
                    OrganizationRow organization = organizationByCode(
                            tenant.id(), normalizedOrganizationCode, true);
                    requireOrganizationCapability(
                            actor, organization, "staff.manage");
                    Set<String> permissions = normalizePermissions(
                            request.permissionCodes());
                    UUID staffUid = UUID.randomUUID();
                    try {
                        jdbc.update("""
                                        INSERT INTO iam_staff_account (
                                            tenant_id, staff_account_uid,
                                            account_kind, login_name,
                                            password_hash, display_name,
                                            contact_phone, enabled,
                                            failed_login_count, locked_until,
                                            auth_version, password_changed_at,
                                            lock_version, created_at, updated_at
                                        ) VALUES (
                                            ?, ?, 'STAFF', ?, ?, ?, ?, 1, 0,
                                            NULL, 0, UTC_TIMESTAMP(3), 0,
                                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                                        )
                                        """,
                                tenant.id(),
                                staffUid.toString(),
                                normalizeLogin(request.loginName()),
                                passwordEncoder.encode(
                                        request.initialPassword()),
                                request.displayName().trim(),
                                blankToNull(request.contactPhone()));
                    } catch (DataIntegrityViolationException conflict) {
                        throw conflict(
                                "IDENTITY.LOGIN_NAME_ALREADY_USED",
                                "登录名已被使用");
                    }
                    StaffRow target = staffByUid(
                            tenant.id(), staffUid, true);
                    validateMembershipAuthorization(
                            actor,
                            organization,
                            target,
                            request.manager(),
                            permissions);
                    jdbc.update("""
                                    INSERT INTO iam_organization_staff_membership (
                                        tenant_id, organization_id,
                                        staff_account_id, is_manager, enabled,
                                        lock_version, created_at, updated_at
                                    ) VALUES (
                                        ?, ?, ?, ?, 1, 0,
                                        UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                                    )
                                    """,
                            tenant.id(),
                            organization.id(),
                            target.id(),
                            request.manager());
                    replaceGrants(
                            tenant.id(),
                            target.id(),
                            organization.id(),
                            "ORGANIZATION",
                            permissions);
                    incrementAuthVersion(tenant.id(), target.id());
                    StaffAccountView staffView = staffView(staffById(
                            tenant.id(), target.id(), false));
                    MembershipView membershipView = membershipView(membership(
                            tenant.id(),
                            organization.id(),
                            target.id(),
                            false));
                    ProvisionedOrganizationStaffView view =
                            new ProvisionedOrganizationStaffView(
                                    staffView, membershipView);
                    return result(
                            view,
                            AuditScopeKind.ORGANIZATION,
                            tenant.id(),
                            organization.id(),
                            "organization-membership",
                            organization.code() + ":" + staffUid,
                            null,
                            view,
                            null);
                });
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public StaffAccountView updateStaffProfile(
            UUID operationUid,
            String tenantCode,
            UUID staffUid,
            UpdateStaffProfileRequest request,
            boolean self) {
        TargetWebActor actor = TargetWebActorContext.required();
        return command(
                operationUid,
                "identity.staff.profile.update",
                "tenant:" + normalizeCode(tenantCode)
                        + "|staff:" + staffUid,
                request,
                () -> replayStaff(tenantCode, staffUid),
                () -> {
                    TenantRow tenant = tenantForRequest(actor, tenantCode, true);
                    StaffRow target = staffByUid(tenant.id(), staffUid, true);
                    if (self) {
                        if (!actor.principalUid().equals(staffUid)) {
                            throw notFound();
                        }
                    } else {
                        requireTenantCapabilityUnlessPlatform(
                                actor, "staff.manage");
                    }
                    requireVersion(target.version(), request.expectedVersion());
                    StaffAccountView before = staffView(target);
                    jdbc.update("""
                                    UPDATE iam_staff_account
                                    SET display_name = ?,
                                        contact_phone = ?,
                                        lock_version = lock_version + 1,
                                        updated_at = UTC_TIMESTAMP(3)
                                    WHERE tenant_id = ?
                                      AND id = ?
                                    """,
                            request.displayName().trim(),
                            blankToNull(request.contactPhone()),
                            tenant.id(),
                            target.id());
                    StaffAccountView after = staffView(staffById(
                            tenant.id(), target.id(), false));
                    return result(
                            after,
                            AuditScopeKind.TENANT,
                            tenant.id(),
                            null,
                            "staff-account",
                            staffUid.toString(),
                            before,
                            after,
                            null);
                });
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public StaffAccountView changeStaffStatus(
            UUID operationUid,
            String tenantCode,
            UUID staffUid,
            AccountVersionCommand request,
            boolean enabled) {
        TargetWebActor actor = TargetWebActorContext.required();
        return command(
                operationUid,
                enabled
                        ? "identity.staff.activate"
                        : "identity.staff.deactivate",
                "tenant:" + normalizeCode(tenantCode)
                        + "|staff:" + staffUid,
                request,
                () -> replayStaff(tenantCode, staffUid),
                () -> {
                    TenantRow tenant = tenantForRequest(actor, tenantCode, true);
                    requireTenantCapabilityUnlessPlatform(
                            actor, "staff.manage");
                    StaffRow target = staffByUid(tenant.id(), staffUid, true);
                    protectPrincipal(target);
                    requireAccountVersions(
                            target,
                            request.expectedVersion(),
                            request.expectedAuthVersion());
                    if (target.enabled() == enabled) {
                        throw conflict(
                                "IDENTITY.STAFF_ACCOUNT_STATE_CONFLICT",
                                "工作人员账号已经处于目标状态");
                    }
                    StaffAccountView before = staffView(target);
                    jdbc.update("""
                                    UPDATE iam_staff_account
                                    SET enabled = ?,
                                        auth_version = auth_version + 1,
                                        lock_version = lock_version + 1,
                                        updated_at = UTC_TIMESTAMP(3)
                                    WHERE tenant_id = ?
                                      AND id = ?
                                    """,
                            enabled,
                            tenant.id(),
                            target.id());
                    sessionRepository.revokeAllStaffSessions(
                            tenant.id(),
                            target.id(),
                            enabled
                                    ? "STAFF_ACCOUNT_REACTIVATED"
                                    : "STAFF_ACCOUNT_DISABLED");
                    StaffAccountView after = staffView(staffById(
                            tenant.id(), target.id(), false));
                    return result(
                            after,
                            AuditScopeKind.TENANT,
                            tenant.id(),
                            null,
                            "staff-account",
                            staffUid.toString(),
                            before,
                            after,
                            request.reason());
                });
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public StaffAccountView resetStaffPassword(
            UUID operationUid,
            String tenantCode,
            UUID staffUid,
            ResetPasswordRequest request) {
        TargetWebActor actor = TargetWebActorContext.required();
        return command(
                operationUid,
                "identity.staff.password-reset",
                "tenant:" + normalizeCode(tenantCode)
                        + "|staff:" + staffUid,
                request,
                () -> replayStaff(tenantCode, staffUid),
                () -> {
                    TenantRow tenant = tenantForRequest(actor, tenantCode, true);
                    requireTenantCapabilityUnlessPlatform(
                            actor, "staff.manage");
                    StaffRow target = staffByUid(tenant.id(), staffUid, true);
                    protectPrincipal(target);
                    requireAccountVersions(
                            target,
                            request.expectedVersion(),
                            request.expectedAuthVersion());
                    StaffAccountView before = staffView(target);
                    updatePassword(target, request.newPassword());
                    StaffAccountView after = staffView(staffById(
                            tenant.id(), target.id(), false));
                    return result(
                            after,
                            AuditScopeKind.TENANT,
                            tenant.id(),
                            null,
                            "staff-account",
                            staffUid.toString(),
                            before,
                            after,
                            null);
                });
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public StaffAccountView changeOwnPassword(
            UUID operationUid,
            ChangeOwnPasswordRequest request) {
        TargetWebActor actor = requireStaffActor();
        return command(
                operationUid,
                "identity.staff.password-change",
                "tenant:" + normalizeCode(actor.tenantCode())
                        + "|staff:" + actor.principalUid(),
                request,
                () -> replayStaff(
                        actor.tenantCode(), actor.principalUid()),
                () -> {
                    TenantRow tenant = tenantById(actor.tenantId(), true);
                    StaffRow target = staffByUid(
                            tenant.id(), actor.principalUid(), true);
                    requireAccountVersions(
                            target,
                            request.expectedVersion(),
                            request.expectedAuthVersion());
                    if (!passwordEncoder.matches(
                            request.currentPassword(), target.passwordHash())) {
                        throw new TargetApiException(
                                403,
                                "AUTH.CURRENT_PASSWORD_INVALID",
                                "当前密码不正确");
                    }
                    StaffAccountView before = staffView(target);
                    updatePassword(target, request.newPassword());
                    StaffAccountView after = staffView(staffById(
                            tenant.id(), target.id(), false));
                    return result(
                            after,
                            AuditScopeKind.TENANT,
                            tenant.id(),
                            null,
                            "staff-account",
                            target.uid().toString(),
                            before,
                            after,
                            null);
                });
    }

    @Transactional(readOnly = true)
    public List<PermissionDefinitionView> permissionDefinitions() {
        TargetWebActor actor = TargetWebActorContext.required();
        if (!actor.platform()
                && !actor.effectiveCapabilities().contains("permission.read")
                && !actor.effectiveCapabilities().contains("permission.manage")
                && !actor.tenantPrincipal()) {
            throw forbidden();
        }
        return jdbc.query("""
                        SELECT permission_code, scope_kind, permission_name,
                               description
                        FROM iam_permission_definition
                        WHERE enabled = 1
                        ORDER BY permission_code, scope_kind
                        """,
                (rs, ignored) -> new PermissionDefinitionView(
                        rs.getString("permission_code"),
                        rs.getString("scope_kind"),
                        rs.getString("permission_name"),
                        rs.getString("description")));
    }

    @Transactional(readOnly = true)
    public EffectiveAccessView effectiveAccess(
            String tenantCode,
            UUID staffUid,
            boolean self) {
        TargetWebActor actor = TargetWebActorContext.required();
        TenantRow tenant = tenantForRequest(actor, tenantCode, false);
        StaffRow target = staffByUid(tenant.id(), staffUid, false);
        if (self) {
            if (!actor.principalUid().equals(staffUid)) {
                throw notFound();
            }
        } else if (!actor.platform()) {
            requireStaffVisible(actor, tenant.id(), target.id());
            boolean tenantAccess =
                    actor.hasTenantCapability("permission.read")
                            || actor.hasTenantCapability("permission.manage");
            if (!tenantAccess
                    && actor.organizations().stream().noneMatch(access ->
                    access.manager()
                            || access.capabilities().contains("permission.read")
                            || access.capabilities().contains(
                            "permission.manage"))) {
                throw forbidden();
            }
            EffectiveAccessView full = effectiveAccessView(tenant, target);
            if (!tenantAccess) {
                Set<String> visibleOrganizations = actor.organizations().stream()
                        .filter(access -> access.manager()
                                || access.capabilities().contains(
                                "permission.read")
                                || access.capabilities().contains(
                                "permission.manage"))
                        .map(OrganizationAccess::organizationCode)
                        .collect(java.util.stream.Collectors.toUnmodifiableSet());
                return new EffectiveAccessView(
                        full.staffAccountUid(),
                        List.of(),
                        full.organizations().stream()
                                .filter(access -> visibleOrganizations.contains(
                                        access.organizationCode()))
                                .toList(),
                        full.authVersion());
            }
        }
        return effectiveAccessView(tenant, target);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public EffectiveAccessView replaceTenantPermissions(
            UUID operationUid,
            String tenantCode,
            UUID staffUid,
            ReplaceTenantPermissionsRequest request) {
        TargetWebActor actor = TargetWebActorContext.required();
        return command(
                operationUid,
                "identity.staff.tenant-permissions.replace",
                "tenant:" + normalizeCode(tenantCode)
                        + "|staff:" + staffUid,
                request,
                () -> replayEffectiveAccess(tenantCode, staffUid),
                () -> {
                    TenantRow tenant = tenantForRequest(actor, tenantCode, true);
                    requireTenantCapabilityUnlessPlatform(
                            actor, "permission.manage");
                    StaffRow target = staffByUid(tenant.id(), staffUid, true);
                    protectPrincipal(target);
                    if (!actor.platform()
                            && actor.principalUid().equals(staffUid)) {
                        throw conflict(
                                "IDENTITY.NATURAL_AUTHORITY_IMMUTABLE",
                                "不能修改自己的授权");
                    }
                    if (target.authVersion()
                            != request.expectedAuthVersion()) {
                        throw versionConflict(target.authVersion());
                    }
                    Set<String> permissions = normalizePermissions(
                            request.permissionCodes());
                    requireDelegableTenant(actor, permissions);
                    EffectiveAccessView before =
                            effectiveAccessView(tenant, target);
                    replaceGrants(
                            tenant.id(),
                            target.id(),
                            null,
                            "TENANT",
                            permissions);
                    incrementAuthVersion(tenant.id(), target.id());
                    sessionRepository.revokeAllStaffSessions(
                            tenant.id(),
                            target.id(),
                            "TENANT_PERMISSIONS_CHANGED");
                    StaffRow changed = staffById(
                            tenant.id(), target.id(), false);
                    EffectiveAccessView after =
                            effectiveAccessView(tenant, changed);
                    return result(
                            after,
                            AuditScopeKind.TENANT,
                            tenant.id(),
                            null,
                            "staff-account",
                            staffUid.toString(),
                            before,
                            after,
                            null);
                });
    }

    @Transactional(readOnly = true)
    public PageData<MembershipView> listMemberships(
            String tenantCode,
            String organizationCode,
            int requestedPage,
            int requestedPageSize) {
        TargetWebActor actor = TargetWebActorContext.required();
        TenantRow tenant = tenantForRequest(actor, tenantCode, false);
        OrganizationRow organization = organizationByCode(
                tenant.id(), normalizeCode(organizationCode), false);
        requireOrganizationCapability(
                actor, organization, "staff.read");
        List<MembershipView> memberships = jdbc.query("""
                        SELECT m.id, m.tenant_id, m.organization_id,
                               m.staff_account_id, m.is_manager, m.enabled,
                               m.lock_version, m.created_at, m.updated_at,
                               s.staff_account_uid, s.display_name,
                               s.auth_version, o.organization_code
                        FROM iam_organization_staff_membership m
                        JOIN iam_staff_account s
                          ON s.tenant_id = m.tenant_id
                         AND s.id = m.staff_account_id
                        JOIN iam_organization o
                          ON o.tenant_id = m.tenant_id
                         AND o.id = m.organization_id
                        WHERE m.tenant_id = ?
                          AND m.organization_id = ?
                        ORDER BY s.login_name
                        """,
                (rs, ignored) -> membershipView(membershipRow(rs)),
                tenant.id(),
                organization.id());
        return page(
                memberships,
                page(requestedPage),
                pageSize(requestedPageSize));
    }

    @Transactional(readOnly = true)
    public MembershipView getMembership(
            String tenantCode,
            String organizationCode,
            UUID staffUid) {
        TargetWebActor actor = TargetWebActorContext.required();
        TenantRow tenant = tenantForRequest(actor, tenantCode, false);
        OrganizationRow organization = organizationByCode(
                tenant.id(), normalizeCode(organizationCode), false);
        if (!actor.platform()
                && !actor.hasOrganizationCapability(
                organization.code(), "staff.read")
                && !actor.hasOrganizationCapability(
                organization.code(), "staff.manage")
                && !actor.hasOrganizationCapability(
                organization.code(), "permission.manage")) {
            throw notFound();
        }
        StaffRow staff = staffByUid(tenant.id(), staffUid, false);
        return membershipView(membership(
                tenant.id(), organization.id(), staff.id(), false));
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public MembershipView createMembership(
            UUID operationUid,
            String tenantCode,
            String organizationCode,
            CreateMembershipRequest request) {
        TargetWebActor actor = TargetWebActorContext.required();
        return command(
                operationUid,
                "identity.membership.create",
                "tenant:" + normalizeCode(tenantCode)
                        + "|organization:" + normalizeCode(organizationCode)
                        + "|staff:" + request.staffAccountUid(),
                request,
                () -> replayMembership(
                        tenantCode,
                        organizationCode,
                        request.staffAccountUid()),
                () -> {
                    TenantRow tenant = tenantForRequest(actor, tenantCode, true);
                    OrganizationRow organization = organizationByCode(
                            tenant.id(), normalizeCode(organizationCode), true);
                    requireOrganizationCapability(
                            actor, organization, "staff.manage");
                    StaffRow target = staffByUid(
                            tenant.id(), request.staffAccountUid(), true);
                    protectPrincipal(target);
                    forbidSelfAuthorization(actor, target.uid());
                    if (target.authVersion()
                            != request.expectedAuthVersion()) {
                        throw versionConflict(target.authVersion());
                    }
                    validateMembershipAuthorization(
                            actor,
                            organization,
                            target,
                            request.manager(),
                            request.permissionCodes());
                    if (membershipOrNull(
                            tenant.id(), organization.id(), target.id(), true)
                            != null) {
                        throw conflict(
                                "IDENTITY.MEMBERSHIP_ALREADY_EXISTS",
                                "该工作人员与机构的任职已经存在");
                    }
                    jdbc.update("""
                                    INSERT INTO iam_organization_staff_membership (
                                        tenant_id, organization_id,
                                        staff_account_id, is_manager, enabled,
                                        lock_version, created_at, updated_at
                                    ) VALUES (
                                        ?, ?, ?, ?, 1, 0,
                                        UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                                    )
                                    """,
                            tenant.id(),
                            organization.id(),
                            target.id(),
                            request.manager());
                    replaceGrants(
                            tenant.id(),
                            target.id(),
                            organization.id(),
                            "ORGANIZATION",
                            normalizePermissions(request.permissionCodes()));
                    incrementAuthVersion(tenant.id(), target.id());
                    sessionRepository.revokeAllStaffSessions(
                            tenant.id(),
                            target.id(),
                            "MEMBERSHIP_CREATED");
                    MembershipView view = membershipView(membership(
                            tenant.id(), organization.id(), target.id(), false));
                    return result(
                            view,
                            AuditScopeKind.ORGANIZATION,
                            tenant.id(),
                            organization.id(),
                            "organization-membership",
                            organization.code() + ":" + target.uid(),
                            null,
                            view,
                            null);
                });
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public MembershipView replaceMembershipAuthorization(
            UUID operationUid,
            String tenantCode,
            String organizationCode,
            UUID staffUid,
            MembershipAuthorizationRequest request) {
        TargetWebActor actor = TargetWebActorContext.required();
        return changeMembership(
                operationUid,
                "identity.membership.authorization.replace",
                tenantCode,
                organizationCode,
                staffUid,
                request,
                request.manager(),
                request.permissionCodes(),
                request.expectedVersion(),
                request.expectedAuthVersion(),
                true,
                null);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public MembershipView deactivateMembership(
            UUID operationUid,
            String tenantCode,
            String organizationCode,
            UUID staffUid,
            AccountVersionCommand request) {
        return changeMembership(
                operationUid,
                "identity.membership.deactivate",
                tenantCode,
                organizationCode,
                staffUid,
                request,
                false,
                List.of(),
                request.expectedVersion(),
                request.expectedAuthVersion(),
                false,
                request.reason());
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public MembershipView activateMembership(
            UUID operationUid,
            String tenantCode,
            String organizationCode,
            UUID staffUid,
            ActivateMembershipRequest request) {
        return changeMembership(
                operationUid,
                "identity.membership.activate",
                tenantCode,
                organizationCode,
                staffUid,
                request,
                request.manager(),
                request.permissionCodes(),
                request.expectedVersion(),
                request.expectedAuthVersion(),
                true,
                request.reason());
    }

    private MembershipView changeMembership(
            UUID operationUid,
            String action,
            String tenantCode,
            String organizationCode,
            UUID staffUid,
            Object request,
            boolean manager,
            Collection<String> permissionCodes,
            long expectedVersion,
            long expectedAuthVersion,
            boolean enabled,
            String reason) {
        TargetWebActor actor = TargetWebActorContext.required();
        return command(
                operationUid,
                action,
                "tenant:" + normalizeCode(tenantCode)
                        + "|organization:" + normalizeCode(organizationCode)
                        + "|staff:" + staffUid,
                request,
                () -> replayMembership(
                        tenantCode, organizationCode, staffUid),
                () -> {
                    TenantRow tenant = tenantForRequest(actor, tenantCode, true);
                    OrganizationRow organization = organizationByCode(
                            tenant.id(), normalizeCode(organizationCode), true);
                    requireOrganizationCapability(
                            actor, organization, "staff.manage");
                    StaffRow target = staffByUid(tenant.id(), staffUid, true);
                    protectPrincipal(target);
                    forbidSelfAuthorization(actor, staffUid);
                    MembershipRow membership = membership(
                            tenant.id(), organization.id(), target.id(), true);
                    if (membership.version() != expectedVersion) {
                        throw versionConflict(membership.version());
                    }
                    if (target.authVersion() != expectedAuthVersion) {
                        throw versionConflict(target.authVersion());
                    }
                    if (enabled) {
                        validateMembershipAuthorization(
                                actor,
                                organization,
                                target,
                                manager,
                                permissionCodes);
                    }
                    MembershipView before = membershipView(membership);
                    jdbc.update("""
                                    UPDATE iam_organization_staff_membership
                                    SET is_manager = ?,
                                        enabled = ?,
                                        lock_version = lock_version + 1,
                                        updated_at = UTC_TIMESTAMP(3)
                                    WHERE id = ?
                                    """,
                            enabled && manager,
                            enabled,
                            membership.id());
                    replaceGrants(
                            tenant.id(),
                            target.id(),
                            organization.id(),
                            "ORGANIZATION",
                            enabled
                                    ? normalizePermissions(permissionCodes)
                                    : Set.of());
                    incrementAuthVersion(tenant.id(), target.id());
                    sessionRepository.revokeAllStaffSessions(
                            tenant.id(),
                            target.id(),
                            enabled
                                    ? "MEMBERSHIP_AUTHORIZATION_CHANGED"
                                    : "MEMBERSHIP_DISABLED");
                    MembershipView after = membershipView(membership(
                            tenant.id(), organization.id(), target.id(), false));
                    return result(
                            after,
                            AuditScopeKind.ORGANIZATION,
                            tenant.id(),
                            organization.id(),
                            "organization-membership",
                            organization.code() + ":" + target.uid(),
                            before,
                            after,
                            reason);
                });
    }

    private void validateMembershipAuthorization(
            TargetWebActor actor,
            OrganizationRow organization,
            StaffRow target,
            boolean manager,
            Collection<String> rawPermissionCodes) {
        Set<String> permissions = normalizePermissions(rawPermissionCodes);
        if (manager && !permissions.isEmpty()) {
            throw new TargetApiException(
                    400,
                    "IDENTITY.MANAGER_DIRECT_PERMISSIONS_FORBIDDEN",
                    "机构负责人不能同时保存直接权限");
        }
        if (manager) {
            requireManagerAppointmentAuthority(actor, organization);
        } else if (!permissions.isEmpty()) {
            requireOrganizationCapability(
                    actor, organization, "permission.manage");
            requireDelegableOrganization(actor, organization, permissions);
        }
        if (!target.enabled()) {
            throw unprocessable(
                    "IDENTITY.STAFF_ACCOUNT_DISABLED",
                    "禁用账号不能建立或恢复任职");
        }
    }

    private EffectiveAccessView effectiveAccessView(
            TenantRow tenant,
            StaffRow staff) {
        boolean principal =
                "TENANT_PRINCIPAL".equals(staff.accountKind());
        List<String> tenantPermissions = principal
                ? permissionCodes("TENANT").stream().sorted().toList()
                : grantCodes(
                        tenant.id(), staff.id(), null, "TENANT")
                        .stream().sorted().toList();
        Set<String> organizationPermissionDefinitions =
                permissionCodes("ORGANIZATION");
        List<OrganizationEffectiveAccess> organizations = new ArrayList<>();
        List<OrganizationRow> rows = jdbc.query("""
                        SELECT id, tenant_id, organization_code,
                               organization_name, status, contact_phone,
                               contact_address, lock_version, created_at,
                               updated_at
                        FROM iam_organization
                        WHERE tenant_id = ?
                        ORDER BY organization_code
                        """,
                (rs, ignored) -> organizationRow(rs),
                tenant.id());
        for (OrganizationRow organization : rows) {
            MembershipRow membership = membershipOrNull(
                    tenant.id(), organization.id(), staff.id(), false);
            boolean manager = principal
                    || (membership != null
                    && membership.enabled()
                    && membership.manager());
            Set<String> permissions = new LinkedHashSet<>(tenantPermissions);
            if (manager) {
                permissions.addAll(organizationPermissionDefinitions);
            } else if (membership != null && membership.enabled()) {
                permissions.addAll(grantCodes(
                        tenant.id(),
                        staff.id(),
                        organization.id(),
                        "ORGANIZATION"));
            }
            if (principal || membership != null || !tenantPermissions.isEmpty()) {
                organizations.add(new OrganizationEffectiveAccess(
                        organization.code(),
                        organization.name(),
                        manager,
                        permissions.stream().sorted().toList()));
            }
        }
        return new EffectiveAccessView(
                staff.uid(),
                tenantPermissions,
                List.copyOf(organizations),
                staff.authVersion());
    }

    private void replaceGrants(
            long tenantId,
            long staffAccountId,
            Long organizationId,
            String scopeKind,
            Set<String> targetPermissionCodes) {
        Map<String, Long> definitions = permissionDefinitionIds(
                scopeKind, targetPermissionCodes);
        Set<String> current = grantCodes(
                tenantId, staffAccountId, organizationId, scopeKind);
        Set<String> toRevoke = new LinkedHashSet<>(current);
        toRevoke.removeAll(targetPermissionCodes);
        Set<String> toGrant = new LinkedHashSet<>(targetPermissionCodes);
        toGrant.removeAll(current);
        for (String code : toRevoke) {
            jdbc.update("""
                            UPDATE iam_staff_permission_grant g
                            JOIN iam_permission_definition p
                              ON p.id = g.permission_definition_id
                             AND p.scope_kind = g.scope_kind
                            SET g.revoked_at = UTC_TIMESTAMP(3)
                            WHERE g.tenant_id = ?
                              AND g.staff_account_id = ?
                              AND g.scope_kind = ?
                              AND ((? IS NULL AND g.organization_id IS NULL)
                                   OR g.organization_id = ?)
                              AND g.revoked_at IS NULL
                              AND p.permission_code = ?
                            """,
                    tenantId,
                    staffAccountId,
                    scopeKind,
                    organizationId,
                    organizationId,
                    code);
        }
        for (String code : toGrant) {
            jdbc.update("""
                            INSERT INTO iam_staff_permission_grant (
                                tenant_id, staff_account_id,
                                permission_definition_id, scope_kind,
                                organization_id, granted_at, revoked_at
                            ) VALUES (?, ?, ?, ?, ?, UTC_TIMESTAMP(3), NULL)
                            """,
                    tenantId,
                    staffAccountId,
                    definitions.get(code),
                    scopeKind,
                    organizationId);
        }
    }

    private Set<String> grantCodes(
            long tenantId,
            long staffAccountId,
            Long organizationId,
            String scopeKind) {
        return Set.copyOf(jdbc.queryForList("""
                        SELECT p.permission_code
                        FROM iam_staff_permission_grant g
                        JOIN iam_permission_definition p
                          ON p.id = g.permission_definition_id
                         AND p.scope_kind = g.scope_kind
                        WHERE g.tenant_id = ?
                          AND g.staff_account_id = ?
                          AND g.scope_kind = ?
                          AND ((? IS NULL AND g.organization_id IS NULL)
                               OR g.organization_id = ?)
                          AND g.revoked_at IS NULL
                          AND p.enabled = 1
                        ORDER BY p.permission_code
                        """,
                String.class,
                tenantId,
                staffAccountId,
                scopeKind,
                organizationId,
                organizationId));
    }

    private Map<String, Long> permissionDefinitionIds(
            String scopeKind,
            Set<String> codes) {
        if (codes.isEmpty()) {
            return Map.of();
        }
        Map<String, Long> result = new LinkedHashMap<>();
        for (String code : codes) {
            List<Long> ids = jdbc.queryForList("""
                            SELECT id
                            FROM iam_permission_definition
                            WHERE permission_code = ?
                              AND scope_kind = ?
                              AND enabled = 1
                            """,
                    Long.class,
                    code,
                    scopeKind);
            if (ids.isEmpty()) {
                throw new TargetApiException(
                        422,
                        "IDENTITY.PERMISSION_NOT_DEFINED",
                        "权限码未在目标作用域定义",
                        false,
                        Map.of("permissionCode", code,
                                "scopeKind", scopeKind));
            }
            result.put(code, ids.getFirst());
        }
        return result;
    }

    private Set<String> permissionCodes(String scopeKind) {
        return Set.copyOf(jdbc.queryForList("""
                        SELECT permission_code
                        FROM iam_permission_definition
                        WHERE scope_kind = ?
                          AND enabled = 1
                        ORDER BY permission_code
                        """,
                String.class,
                scopeKind));
    }

    private void updatePassword(StaffRow target, String newPassword) {
        jdbc.update("""
                        UPDATE iam_staff_account
                        SET password_hash = ?,
                            password_changed_at = UTC_TIMESTAMP(3),
                            auth_version = auth_version + 1,
                            lock_version = lock_version + 1,
                            updated_at = UTC_TIMESTAMP(3)
                        WHERE tenant_id = ?
                          AND id = ?
                        """,
                passwordEncoder.encode(newPassword),
                target.tenantId(),
                target.id());
        sessionRepository.revokeAllStaffSessions(
                target.tenantId(),
                target.id(),
                "PASSWORD_CHANGED");
    }

    private void incrementAuthVersion(long tenantId, long staffAccountId) {
        jdbc.update("""
                        UPDATE iam_staff_account
                        SET auth_version = auth_version + 1,
                            updated_at = UTC_TIMESTAMP(3)
                        WHERE tenant_id = ?
                          AND id = ?
                        """,
                tenantId, staffAccountId);
    }

    private <T> T command(
            UUID operationUid,
            String actionCode,
            String targetIdentity,
            Object request,
            Supplier<T> replayWork,
            Supplier<CommandResult<T>> work) {
        validateOperationUid(operationUid);
        TargetWebAuditRequestContext.describe(actionCode, targetIdentity);
        TargetWebActor actor = TargetWebActorContext.required();
        lockAndRevalidateActor(actor);
        String fingerprint = fingerprint(
                actor, actionCode, targetIdentity, request);
        Optional<SuccessfulAudit> prior =
                auditPort.findSuccessful(operationUid);
        if (prior.isPresent()) {
            return replay(
                    prior.get(),
                    actor,
                    actionCode,
                    fingerprint,
                    replayWork);
        }
        CommandResult<T> result = work.get();
        String safeSummary = writeJson(new SafeChange(
                fingerprint,
                safeAuditSnapshot(result.before()),
                safeAuditSnapshot(result.after()),
                Map.of("reasonPresent",
                        result.reason() != null && !result.reason().isBlank())));
        auditPort.append(new AuditEntry(
                UUID.randomUUID(),
                UUID.randomUUID(),
                operationUid,
                result.scopeKind(),
                result.tenantId(),
                result.organizationId(),
                actor.platform()
                        ? AuditActorKind.PLATFORM_ADMIN
                        : AuditActorKind.STAFF_ACCOUNT,
                actor.platform() ? actor.principalId() : null,
                actor.platform() ? null : actor.principalId(),
                null,
                actor.displayName(),
                actionCode,
                result.targetType(),
                result.targetStableKey(),
                "WEB",
                "SUCCEEDED",
                actor.sessionUid(),
                blankToNull(result.reason()),
                safeSummary,
                Instant.now()));
        return result.response();
    }

    /**
     * Serializes commands from one authenticated actor and closes the race
     * between request-filter session resolution and transactional
     * authorization. It also makes same-actor idempotent retries observe the
     * first committed audit row before performing business work.
     */
    private void lockAndRevalidateActor(TargetWebActor actor) {
        boolean valid;
        if (actor.platform()) {
            valid = jdbc.query("""
                            SELECT enabled, auth_version
                            FROM iam_platform_admin
                            WHERE id = ?
                            FOR UPDATE
                            """,
                    (rs, ignored) -> rs.getBoolean("enabled")
                            && rs.getLong("auth_version")
                            == actor.authVersion(),
                    actor.principalId()).stream()
                    .findFirst()
                    .orElse(false);
        } else {
            jdbc.queryForObject("""
                            SELECT id
                            FROM iam_tenant
                            WHERE id = ?
                            FOR UPDATE
                            """,
                    Long.class,
                    actor.tenantId());
            valid = jdbc.query("""
                            SELECT s.enabled, s.auth_version,
                                   t.status AS tenant_status
                            FROM iam_staff_account s
                            JOIN iam_tenant t ON t.id = s.tenant_id
                            WHERE s.tenant_id = ?
                              AND s.id = ?
                            FOR UPDATE
                            """,
                    (rs, ignored) -> rs.getBoolean("enabled")
                            && rs.getLong("auth_version")
                            == actor.authVersion()
                            && "ENABLED".equals(
                            rs.getString("tenant_status")),
                    actor.tenantId(),
                    actor.principalId()).stream()
                    .findFirst()
                    .orElse(false);
        }
        if (!valid) {
            throw new TargetApiException(
                    401,
                    "AUTH.SESSION_INVALID",
                    "登录状态已经变化，请重新登录");
        }
    }

    private <T> T replay(
            SuccessfulAudit audit,
            TargetWebActor actor,
            String actionCode,
            String fingerprint,
            Supplier<T> replayWork) {
        boolean sameActor = actor.platform()
                ? audit.actorKind() == AuditActorKind.PLATFORM_ADMIN
                && Objects.equals(audit.platformAdminId(), actor.principalId())
                : audit.actorKind() == AuditActorKind.STAFF_ACCOUNT
                && Objects.equals(audit.staffAccountId(), actor.principalId());
        JsonNode summary = readJson(audit.safeChangeSummaryJson());
        if (!sameActor
                || !audit.actionCode().equals(actionCode)
                || !fingerprint.equals(summary.path("fingerprint").asText())) {
            throw conflict(
                    "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                    "相同操作标识已绑定到不同请求");
        }
        return replayWork.get();
    }

    private String fingerprint(
            TargetWebActor actor,
            String actionCode,
            String targetIdentity,
            Object request) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            digest.update(actor.principalUid().toString()
                    .getBytes(StandardCharsets.UTF_8));
            digest.update((byte) 0);
            digest.update(actionCode.getBytes(StandardCharsets.UTF_8));
            digest.update((byte) 0);
            digest.update(targetIdentity.getBytes(StandardCharsets.UTF_8));
            digest.update((byte) 0);
            digest.update(objectMapper.writeValueAsBytes(request));
            return HexFormat.of().formatHex(digest.digest());
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
    }

    private String writeJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "safe audit summary cannot be encoded", exception);
        }
    }

    private JsonNode safeAuditSnapshot(Object value) {
        JsonNode snapshot = objectMapper.valueToTree(value);
        redactSensitiveAuditFields(snapshot);
        return snapshot;
    }

    private void redactSensitiveAuditFields(JsonNode node) {
        if (node == null || node.isNull()) {
            return;
        }
        if (node.isArray()) {
            for (int index = 0; index < node.size(); index++) {
                redactSensitiveAuditFields(node.get(index));
            }
            return;
        }
        if (!node.isObject()) {
            return;
        }
        ObjectNode object = (ObjectNode) node;
        for (String name : List.copyOf(object.propertyNames())) {
            if (sensitiveAuditField(name)) {
                object.put(name, "[REDACTED]");
            } else {
                redactSensitiveAuditFields(object.get(name));
            }
        }
    }

    private static boolean sensitiveAuditField(String name) {
        String normalized = name.toLowerCase(Locale.ROOT)
                .replace("_", "")
                .replace("-", "");
        return normalized.contains("password")
                || normalized.contains("phone")
                || normalized.contains("address")
                || normalized.contains("openid")
                || normalized.contains("token")
                || normalized.contains("secret")
                || normalized.contains("credential")
                || normalized.endsWith("url");
    }

    private JsonNode readJson(String value) {
        try {
            return objectMapper.readTree(value);
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "safe audit summary cannot be decoded", exception);
        }
    }

    private TenantView replayTenant(String tenantCode) {
        return tenantView(tenantByCode(normalizeCode(tenantCode), false));
    }

    private StaffAccountView replayPrincipal(String tenantCode) {
        TenantRow tenant = tenantByCode(normalizeCode(tenantCode), false);
        StaffRow principal = principalForTenant(tenant.id());
        if (principal == null) {
            throw notFound();
        }
        return staffView(principal);
    }

    private OrganizationView replayOrganization(
            String tenantCode,
            String organizationCode) {
        TenantRow tenant = tenantByCode(normalizeCode(tenantCode), false);
        return organizationView(organizationByCode(
                tenant.id(), normalizeCode(organizationCode), false));
    }

    private StaffAccountView replayStaff(
            String tenantCode,
            UUID staffUid) {
        TenantRow tenant = tenantByCode(normalizeCode(tenantCode), false);
        return staffView(staffByUid(tenant.id(), staffUid, false));
    }

    private StaffAccountView replayStaffByLogin(
            String tenantCode,
            String loginName) {
        TenantRow tenant = tenantByCode(normalizeCode(tenantCode), false);
        return staffView(staffByLogin(
                tenant.id(), normalizeLogin(loginName), false));
    }

    private ProvisionedOrganizationStaffView replayProvisionedStaff(
            String tenantCode,
            String organizationCode,
            String loginName) {
        TenantRow tenant = tenantByCode(normalizeCode(tenantCode), false);
        OrganizationRow organization = organizationByCode(
                tenant.id(), normalizeCode(organizationCode), false);
        StaffRow staff = staffByLogin(
                tenant.id(), normalizeLogin(loginName), false);
        return new ProvisionedOrganizationStaffView(
                staffView(staff),
                membershipView(membership(
                        tenant.id(),
                        organization.id(),
                        staff.id(),
                        false)));
    }

    private EffectiveAccessView replayEffectiveAccess(
            String tenantCode,
            UUID staffUid) {
        TenantRow tenant = tenantByCode(normalizeCode(tenantCode), false);
        return effectiveAccessView(
                tenant, staffByUid(tenant.id(), staffUid, false));
    }

    private MembershipView replayMembership(
            String tenantCode,
            String organizationCode,
            UUID staffUid) {
        TenantRow tenant = tenantByCode(normalizeCode(tenantCode), false);
        OrganizationRow organization = organizationByCode(
                tenant.id(), normalizeCode(organizationCode), false);
        StaffRow staff = staffByUid(tenant.id(), staffUid, false);
        return membershipView(membership(
                tenant.id(), organization.id(), staff.id(), false));
    }

    private TenantRow tenantForRequest(
            TargetWebActor actor,
            String requestedTenantCode,
            boolean forUpdate) {
        String code = actor.platform()
                ? normalizeCode(requestedTenantCode)
                : actor.tenantCode();
        TenantRow tenant = tenantByCode(code, forUpdate);
        if (!actor.platform()) {
            requireSameTenant(actor, tenant.id());
        }
        return tenant;
    }

    private TenantRow tenantByCode(String code, boolean forUpdate) {
        return jdbc.query("""
                        SELECT id, tenant_code, enterprise_name, status,
                               contact_name, contact_phone, contact_address,
                               lock_version, created_at, updated_at
                        FROM iam_tenant
                        WHERE tenant_code = ?
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> tenantRow(rs),
                code).stream().findFirst().orElseThrow(
                TargetIdentityDirectoryService::notFound);
    }

    private TenantRow tenantById(long id, boolean forUpdate) {
        return jdbc.query("""
                        SELECT id, tenant_code, enterprise_name, status,
                               contact_name, contact_phone, contact_address,
                               lock_version, created_at, updated_at
                        FROM iam_tenant
                        WHERE id = ?
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> tenantRow(rs),
                id).stream().findFirst().orElseThrow(
                TargetIdentityDirectoryService::notFound);
    }

    private OrganizationRow organizationByCode(
            long tenantId,
            String code,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT id, tenant_id, organization_code,
                               organization_name, status, contact_phone,
                               contact_address, lock_version, created_at,
                               updated_at
                        FROM iam_organization
                        WHERE tenant_id = ?
                          AND organization_code = ?
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> organizationRow(rs),
                tenantId, code).stream().findFirst().orElseThrow(
                TargetIdentityDirectoryService::notFound);
    }

    private OrganizationRow organizationById(
            long tenantId,
            long organizationId,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT id, tenant_id, organization_code,
                               organization_name, status, contact_phone,
                               contact_address, lock_version, created_at,
                               updated_at
                        FROM iam_organization
                        WHERE tenant_id = ?
                          AND id = ?
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> organizationRow(rs),
                tenantId, organizationId).stream().findFirst().orElseThrow(
                TargetIdentityDirectoryService::notFound);
    }

    private StaffRow principalForTenant(long tenantId) {
        return jdbc.query("""
                        SELECT id, tenant_id, staff_account_uid, account_kind,
                               login_name, password_hash, display_name,
                               contact_phone, enabled, auth_version,
                               lock_version, created_at, updated_at
                        FROM iam_staff_account
                        WHERE tenant_id = ?
                          AND account_kind = 'TENANT_PRINCIPAL'
                        """,
                (rs, ignored) -> staffRow(rs),
                tenantId).stream().findFirst().orElse(null);
    }

    private StaffRow staffByUid(
            long tenantId,
            UUID uid,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT id, tenant_id, staff_account_uid, account_kind,
                               login_name, password_hash, display_name,
                               contact_phone, enabled, auth_version,
                               lock_version, created_at, updated_at
                        FROM iam_staff_account
                        WHERE tenant_id = ?
                          AND staff_account_uid = ?
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> staffRow(rs),
                tenantId, uid.toString()).stream().findFirst().orElseThrow(
                TargetIdentityDirectoryService::notFound);
    }

    private StaffRow staffByLogin(
            long tenantId,
            String loginName,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT id, tenant_id, staff_account_uid, account_kind,
                               login_name, password_hash, display_name,
                               contact_phone, enabled, auth_version,
                               lock_version, created_at, updated_at
                        FROM iam_staff_account
                        WHERE tenant_id = ?
                          AND login_name = ?
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> staffRow(rs),
                tenantId, loginName).stream().findFirst().orElseThrow(
                TargetIdentityDirectoryService::notFound);
    }

    private StaffRow staffById(
            long tenantId,
            long staffId,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT id, tenant_id, staff_account_uid, account_kind,
                               login_name, password_hash, display_name,
                               contact_phone, enabled, auth_version,
                               lock_version, created_at, updated_at
                        FROM iam_staff_account
                        WHERE tenant_id = ?
                          AND id = ?
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> staffRow(rs),
                tenantId, staffId).stream().findFirst().orElseThrow(
                TargetIdentityDirectoryService::notFound);
    }

    private MembershipRow membership(
            long tenantId,
            long organizationId,
            long staffId,
            boolean forUpdate) {
        MembershipRow row = membershipOrNull(
                tenantId, organizationId, staffId, forUpdate);
        if (row == null) {
            throw notFound();
        }
        return row;
    }

    private MembershipRow membershipOrNull(
            long tenantId,
            long organizationId,
            long staffId,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT m.id, m.tenant_id, m.organization_id,
                               m.staff_account_id, m.is_manager, m.enabled,
                               m.lock_version, m.created_at, m.updated_at,
                               s.staff_account_uid, s.display_name,
                               s.auth_version, o.organization_code
                        FROM iam_organization_staff_membership m
                        JOIN iam_staff_account s
                          ON s.tenant_id = m.tenant_id
                         AND s.id = m.staff_account_id
                        JOIN iam_organization o
                          ON o.tenant_id = m.tenant_id
                         AND o.id = m.organization_id
                        WHERE m.tenant_id = ?
                          AND m.organization_id = ?
                          AND m.staff_account_id = ?
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> membershipRow(rs),
                tenantId, organizationId, staffId)
                .stream().findFirst().orElse(null);
    }

    private TenantView tenantView(TenantRow row) {
        StaffRow principal = principalForTenant(row.id());
        return new TenantView(
                row.code(),
                row.name(),
                row.status(),
                row.contactName(),
                row.contactPhone(),
                row.contactAddress(),
                row.version(),
                principal == null ? null : new PrincipalAccountSummary(
                        principal.uid(),
                        principal.enabled() ? "ENABLED" : "DISABLED",
                        principal.version(),
                        principal.authVersion()),
                row.createdAt(),
                row.updatedAt());
    }

    private static OrganizationView organizationView(OrganizationRow row) {
        return new OrganizationView(
                row.code(),
                row.name(),
                row.status(),
                row.contactPhone(),
                row.contactAddress(),
                row.version(),
                row.createdAt(),
                row.updatedAt());
    }

    private static StaffAccountView staffView(StaffRow row) {
        return new StaffAccountView(
                row.uid(),
                row.accountKind(),
                row.loginName(),
                row.displayName(),
                row.contactPhone(),
                row.enabled() ? "ENABLED" : "DISABLED",
                row.version(),
                row.authVersion(),
                row.createdAt(),
                row.updatedAt());
    }

    private MembershipView membershipView(MembershipRow row) {
        return new MembershipView(
                row.organizationCode(),
                row.staffUid(),
                row.displayName(),
                row.manager(),
                row.enabled() ? "ENABLED" : "DISABLED",
                row.enabled() && !row.manager()
                        ? grantCodes(
                                row.tenantId(),
                                row.staffId(),
                                row.organizationId(),
                                "ORGANIZATION")
                        .stream().sorted().toList()
                        : List.of(),
                row.version(),
                row.authVersion(),
                row.createdAt(),
                row.updatedAt());
    }

    private Set<Long> visibleStaffIds(TargetWebActor actor, long tenantId) {
        Set<Long> organizationIds = actor.organizations().stream()
                .filter(org -> org.manager()
                        || org.capabilities().contains("staff.read"))
                .map(OrganizationAccess::organizationId)
                .collect(java.util.stream.Collectors.toSet());
        if (organizationIds.isEmpty()) {
            return Set.of();
        }
        Set<Long> result = new LinkedHashSet<>();
        for (Long organizationId : organizationIds) {
            result.addAll(jdbc.queryForList("""
                            SELECT staff_account_id
                            FROM iam_organization_staff_membership
                            WHERE tenant_id = ?
                              AND organization_id = ?
                              AND enabled = 1
                            """,
                    Long.class, tenantId, organizationId));
        }
        return Set.copyOf(result);
    }

    private void requireStaffVisible(
            TargetWebActor actor,
            long tenantId,
            long targetStaffId) {
        if (actor.platform()
                || actor.hasTenantCapability("staff.read")
                || actor.principalId() == targetStaffId) {
            return;
        }
        if (!visibleStaffIds(actor, tenantId).contains(targetStaffId)) {
            throw notFound();
        }
    }

    private static TenantRow tenantRow(ResultSet rs) throws SQLException {
        return new TenantRow(
                rs.getLong("id"),
                rs.getString("tenant_code"),
                rs.getString("enterprise_name"),
                rs.getString("status"),
                rs.getString("contact_name"),
                rs.getString("contact_phone"),
                rs.getString("contact_address"),
                rs.getLong("lock_version"),
                instant(rs, "created_at"),
                instant(rs, "updated_at"));
    }

    private static OrganizationRow organizationRow(ResultSet rs)
            throws SQLException {
        return new OrganizationRow(
                rs.getLong("id"),
                rs.getLong("tenant_id"),
                rs.getString("organization_code"),
                rs.getString("organization_name"),
                rs.getString("status"),
                rs.getString("contact_phone"),
                rs.getString("contact_address"),
                rs.getLong("lock_version"),
                instant(rs, "created_at"),
                instant(rs, "updated_at"));
    }

    private static StaffRow staffRow(ResultSet rs) throws SQLException {
        return new StaffRow(
                rs.getLong("id"),
                rs.getLong("tenant_id"),
                UUID.fromString(rs.getString("staff_account_uid")),
                rs.getString("account_kind"),
                rs.getString("login_name"),
                rs.getString("password_hash"),
                rs.getString("display_name"),
                rs.getString("contact_phone"),
                rs.getBoolean("enabled"),
                rs.getLong("auth_version"),
                rs.getLong("lock_version"),
                instant(rs, "created_at"),
                instant(rs, "updated_at"));
    }

    private static MembershipRow membershipRow(ResultSet rs)
            throws SQLException {
        return new MembershipRow(
                rs.getLong("id"),
                rs.getLong("tenant_id"),
                rs.getLong("organization_id"),
                rs.getLong("staff_account_id"),
                rs.getBoolean("is_manager"),
                rs.getBoolean("enabled"),
                rs.getLong("lock_version"),
                instant(rs, "created_at"),
                instant(rs, "updated_at"),
                UUID.fromString(rs.getString("staff_account_uid")),
                rs.getString("display_name"),
                rs.getLong("auth_version"),
                rs.getString("organization_code"));
    }

    private static Instant instant(ResultSet rs, String column)
            throws SQLException {
        return rs.getObject(column, LocalDateTime.class)
                .toInstant(ZoneOffset.UTC);
    }

    private static String normalizeCode(String value) {
        if (value == null || value.isBlank()) {
            throw invalid("公开编码不能为空");
        }
        return value.trim().toLowerCase(Locale.ROOT);
    }

    private static String normalizeLogin(String value) {
        return value.trim().toLowerCase(Locale.ROOT);
    }

    private static Set<String> normalizePermissions(
            Collection<String> values) {
        if (values == null) {
            return Set.of();
        }
        LinkedHashSet<String> result = new LinkedHashSet<>();
        for (String value : values) {
            if (value == null || value.isBlank()) {
                throw invalid("权限码不能为空");
            }
            result.add(value.trim().toLowerCase(Locale.ROOT));
        }
        return Set.copyOf(result);
    }

    private static String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value.trim();
    }

    private static int page(int value) {
        return value <= 0 ? 1 : value;
    }

    private static int pageSize(int value) {
        return value <= 0
                ? DEFAULT_PAGE_SIZE
                : Math.min(value, MAX_PAGE_SIZE);
    }

    private static <T> PageData<T> page(
            List<T> items,
            int page,
            int pageSize) {
        int from = Math.min((page - 1) * pageSize, items.size());
        int to = Math.min(from + pageSize, items.size());
        return new PageData<>(
                List.copyOf(items.subList(from, to)),
                page,
                pageSize,
                items.size());
    }

    private static void validateOperationUid(UUID operationUid) {
        if (operationUid == null || operationUid.version() != 4) {
            throw invalid("Idempotency-Key 必须是 UUIDv4");
        }
    }

    private static void requireVersion(long current, long expected) {
        if (current != expected) {
            throw versionConflict(current);
        }
    }

    private static void requireAccountVersions(
            StaffRow target,
            long expectedVersion,
            long expectedAuthVersion) {
        if (target.version() != expectedVersion) {
            throw versionConflict(target.version());
        }
        if (target.authVersion() != expectedAuthVersion) {
            throw versionConflict(target.authVersion());
        }
    }

    private static TargetWebActor requirePlatform() {
        TargetWebActor actor = TargetWebActorContext.required();
        if (!actor.platform()) {
            throw forbidden();
        }
        return actor;
    }

    private static TargetWebActor requireStaffActor() {
        TargetWebActor actor = TargetWebActorContext.required();
        if (actor.platform()) {
            throw forbidden();
        }
        return actor;
    }

    private static void requireSameTenant(
            TargetWebActor actor,
            long tenantId) {
        if (actor.tenantId() == null || actor.tenantId() != tenantId) {
            throw notFound();
        }
    }

    private static void requireTenantCapability(
            TargetWebActor actor,
            String... alternatives) {
        if (actor.platform() || actor.tenantPrincipal()) {
            return;
        }
        for (String capability : alternatives) {
            if (actor.hasTenantCapability(capability)) {
                return;
            }
        }
        throw forbidden();
    }

    private static void requireTenantCapabilityUnlessPlatform(
            TargetWebActor actor,
            String capability) {
        if (!actor.platform()) {
            requireTenantCapability(actor, capability);
        }
    }

    private static void requireOrganizationCapability(
            TargetWebActor actor,
            OrganizationRow organization,
            String capability) {
        if (actor.platform()
                || actor.hasOrganizationCapability(
                organization.code(), capability)) {
            return;
        }
        if (actor.organization(organization.code()) == null
                && !actor.hasTenantCapability(capability)) {
            throw notFound();
        }
        throw forbidden();
    }

    private static void requireAnyOrganizationCapability(
            TargetWebActor actor,
            String capability) {
        if (actor.organizations().stream().anyMatch(org ->
                org.manager() || org.capabilities().contains(capability))) {
            return;
        }
        throw forbidden();
    }

    private static void requireDelegableTenant(
            TargetWebActor actor,
            Set<String> permissions) {
        if (actor.platform() || actor.tenantPrincipal()) {
            return;
        }
        if (!actor.tenantCapabilities().containsAll(permissions)) {
            throw unprocessable(
                    "IDENTITY.PERMISSION_NOT_DELEGABLE",
                    "不能授予操作者在租户范围不具备的权限");
        }
    }

    private static void requireDelegableOrganization(
            TargetWebActor actor,
            OrganizationRow organization,
            Set<String> permissions) {
        if (actor.platform() || actor.tenantPrincipal()) {
            return;
        }
        for (String permission : permissions) {
            if (!actor.hasOrganizationCapability(
                    organization.code(), permission)) {
                throw unprocessable(
                        "IDENTITY.PERMISSION_NOT_DELEGABLE",
                        "不能授予操作者在机构范围不具备的权限");
            }
        }
    }

    private static void requireManagerAppointmentAuthority(
            TargetWebActor actor,
            OrganizationRow organization) {
        if (actor.platform()
                || actor.tenantPrincipal()
                || actor.hasTenantCapability("organization-manager.manage")) {
            return;
        }
        OrganizationAccess access = actor.organization(organization.code());
        if (access != null && access.manager()) {
            return;
        }
        throw forbidden();
    }

    private static void protectPrincipal(StaffRow target) {
        if ("TENANT_PRINCIPAL".equals(target.accountKind())) {
            throw conflict(
                    "IDENTITY.TENANT_PRINCIPAL_PROTECTED",
                    "租户主体账号只能通过平台主体专用入口变更");
        }
    }

    private static void forbidSelfAuthorization(
            TargetWebActor actor,
            UUID staffUid) {
        if (!actor.platform() && actor.principalUid().equals(staffUid)) {
            throw conflict(
                    "IDENTITY.NATURAL_AUTHORITY_IMMUTABLE",
                    "不能修改自己的任职或授权");
        }
    }

    private static TargetApiException invalid(String message) {
        return new TargetApiException(
                400, "COMMON.INVALID_REQUEST", message);
    }

    private static TargetApiException forbidden() {
        return new TargetApiException(
                403,
                "AUTH.CAPABILITY_REQUIRED",
                "当前账号缺少所需能力");
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404,
                "RESOURCE.NOT_FOUND",
                "资源不存在或不在当前可见范围");
    }

    private static TargetApiException conflict(String code, String message) {
        return new TargetApiException(409, code, message);
    }

    private static TargetApiException versionConflict(long currentVersion) {
        return new TargetApiException(
                409,
                "COMMON.VERSION_CONFLICT",
                "资源版本已变化，请重新查询",
                false,
                Map.of("currentVersion", currentVersion));
    }

    private static TargetApiException unprocessable(
            String code,
            String message) {
        return new TargetApiException(422, code, message);
    }

    private static <T> CommandResult<T> result(
            T response,
            AuditScopeKind scopeKind,
            Long tenantId,
            Long organizationId,
            String targetType,
            String targetStableKey,
            Object before,
            Object after,
            String reason) {
        return new CommandResult<>(
                response,
                scopeKind,
                tenantId,
                organizationId,
                targetType,
                targetStableKey,
                before,
                after,
                reason);
    }

    private record CommandResult<T>(
            T response,
            AuditScopeKind scopeKind,
            Long tenantId,
            Long organizationId,
            String targetType,
            String targetStableKey,
            Object before,
            Object after,
            String reason) {
    }

    private record TenantRow(
            long id,
            String code,
            String name,
            String status,
            String contactName,
            String contactPhone,
            String contactAddress,
            long version,
            Instant createdAt,
            Instant updatedAt) {
    }

    private record OrganizationRow(
            long id,
            long tenantId,
            String code,
            String name,
            String status,
            String contactPhone,
            String contactAddress,
            long version,
            Instant createdAt,
            Instant updatedAt) {
    }

    private record StaffRow(
            long id,
            long tenantId,
            UUID uid,
            String accountKind,
            String loginName,
            String passwordHash,
            String displayName,
            String contactPhone,
            boolean enabled,
            long authVersion,
            long version,
            Instant createdAt,
            Instant updatedAt) {
    }

    private record MembershipRow(
            long id,
            long tenantId,
            long organizationId,
            long staffId,
            boolean manager,
            boolean enabled,
            long version,
            Instant createdAt,
            Instant updatedAt,
            UUID staffUid,
            String displayName,
            long authVersion,
            String organizationCode) {
    }
}
