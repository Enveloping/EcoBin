package org.enveloping.ecobin.identity.application.directory;

import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.audit.SuccessfulAudit;
import org.enveloping.ecobin.identity.api.port.OrganizationUserRegistrationSourceQueryPort;
import org.enveloping.ecobin.identity.api.port.OrganizationUserRegistrationSourceQueryPort.RegistrationSourceQuery;
import org.enveloping.ecobin.identity.api.port.OrganizationUserRegistrationSourceQueryPort.RegistrationSourceSummary;
import org.enveloping.ecobin.identity.api.port.OrganizationUserRegistrationSourceQueryPort.RegistrationSourceUsersQuery;
import org.enveloping.ecobin.identity.application.web.TargetWebActor;
import org.enveloping.ecobin.identity.application.web.TargetWebActorContext;
import org.enveloping.ecobin.framework.web.TargetWebAuditRequestContext;
import org.enveloping.ecobin.identity.infrastructure.persistence.v1.TargetIdentitySessionRepository;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.AccountVersionCommand;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.BindingSnapshot;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.OrganizationUserCurrentMiniappBinding;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.OrganizationUserLookupView;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.OrganizationUserRegistrationSource;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.OrganizationUserView;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.PageData;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.SetStaffMiniappBindingRequest;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.StaffCurrentMiniappBinding;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.StaffMiniappBindingLookupView;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.StaffMiniappBindingView;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.VersionCommand;
import org.springframework.dao.DataIntegrityViolationException;
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
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.function.Supplier;
import java.util.regex.Pattern;

@Service
public class TargetOrganizationUserBindingService {

    private static final Pattern E164 = Pattern.compile("^\\+[1-9][0-9]{1,14}$");
    private static final int DEFAULT_PAGE_SIZE = 20;
    private static final int MAX_PAGE_SIZE = 200;

    private final JdbcTemplate jdbc;
    private final TargetIdentitySessionRepository sessionRepository;
    private final OrganizationUserRegistrationSourceQueryPort sourceQuery;
    private final AuditPort auditPort;
    private final ObjectMapper objectMapper;

    public TargetOrganizationUserBindingService(
            JdbcTemplate jdbc,
            TargetIdentitySessionRepository sessionRepository,
            OrganizationUserRegistrationSourceQueryPort sourceQuery,
            AuditPort auditPort,
            ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.sessionRepository = sessionRepository;
        this.sourceQuery = sourceQuery;
        this.auditPort = auditPort;
        this.objectMapper = objectMapper;
    }

    @Transactional(readOnly = true)
    public OrganizationUserLookupView lookupByPhone(
            String tenantCode,
            String organizationCode,
            String phoneNumber) {
        Scope scope = scope(tenantCode, organizationCode, false);
        requireCapability(scope, "user.read");
        UserRow user = jdbc.query("""
                        SELECT id, organization_user_uid, phone_e164,
                               phone_bound_at, nickname, status,
                               registered_at, lock_version
                        FROM iam_organization_user
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND phone_e164 = ?
                        """,
                (rs, ignored) -> userRow(rs),
                scope.tenantId(),
                scope.organizationId(),
                normalizePhone(phoneNumber)).stream()
                .findFirst()
                .orElseThrow(TargetOrganizationUserBindingService::notFound);
        BindingRow current = activeUserBinding(user.id(), false);
        return userLookup(user, current);
    }

    @Transactional(readOnly = true)
    public PageData<OrganizationUserView> listOrganizationUsers(
            String tenantCode,
            String organizationCode,
            int requestedPage,
            int requestedPageSize,
            String requestedStatus,
            Boolean phoneBound,
            Instant registeredFrom,
            Instant registeredTo,
            String sourceDeviceCode,
            Boolean cleanOperationEnabled) {
        Scope scope = scope(tenantCode, organizationCode, false);
        requireCapability(scope, "user.read");
        int page = requestedPage <= 0 ? 1 : requestedPage;
        int pageSize = requestedPageSize <= 0
                ? DEFAULT_PAGE_SIZE
                : Math.min(requestedPageSize, MAX_PAGE_SIZE);
        String status = normalizeUserStatus(requestedStatus);
        if (registeredFrom != null
                && registeredTo != null
                && !registeredFrom.isBefore(registeredTo)) {
            throw invalidRequest();
        }
        String deviceCode = blankToNull(sourceDeviceCode);
        Set<UUID> sourceUsers = null;
        if (deviceCode != null) {
            sourceUsers = sourceQuery.findOrganizationUsers(
                    new RegistrationSourceUsersQuery(
                            scope.tenantCode(),
                            scope.organizationCode(),
                            deviceCode));
            if (sourceUsers.isEmpty()) {
                return new PageData<>(
                        List.of(), page, pageSize, 0);
            }
        }

        StringBuilder predicate = new StringBuilder("""
                FROM iam_organization_user u
                WHERE u.tenant_id = ?
                  AND u.organization_id = ?
                  AND u.miniapp_channel_id = ?
                """);
        List<Object> parameters = new ArrayList<>();
        parameters.add(scope.tenantId());
        parameters.add(scope.organizationId());
        parameters.add(scope.miniappId());
        if (status != null) {
            predicate.append(" AND u.status = ?");
            parameters.add(status);
        }
        if (phoneBound != null) {
            predicate.append(phoneBound
                    ? " AND u.phone_e164 IS NOT NULL"
                    + " AND u.phone_bound_at IS NOT NULL"
                    : " AND u.phone_e164 IS NULL"
                    + " AND u.phone_bound_at IS NULL");
        }
        if (registeredFrom != null) {
            predicate.append(" AND u.registered_at >= ?");
            parameters.add(LocalDateTime.ofInstant(
                    registeredFrom, ZoneOffset.UTC));
        }
        if (registeredTo != null) {
            predicate.append(" AND u.registered_at < ?");
            parameters.add(LocalDateTime.ofInstant(
                    registeredTo, ZoneOffset.UTC));
        }
        if (sourceUsers != null) {
            predicate.append(" AND u.organization_user_uid IN (");
            predicate.append(sourceUsers.stream()
                    .map(ignored -> "?")
                    .collect(java.util.stream.Collectors.joining(", ")));
            predicate.append(")");
            sourceUsers.forEach(
                    uid -> parameters.add(uid.toString()));
        }
        if (cleanOperationEnabled != null) {
            predicate.append(cleanOperationEnabled
                    ? " AND EXISTS ("
                    : " AND NOT EXISTS (");
            predicate.append("""
                    SELECT 1
                    FROM iam_organization_user_capability c
                    WHERE c.tenant_id = u.tenant_id
                      AND c.organization_id = u.organization_id
                      AND c.organization_user_id = u.id
                      AND c.capability_code = 'CLEAN_OPERATION'
                      AND c.enabled = 1
                    )
                    """);
        }

        long total = jdbc.queryForObject(
                "SELECT COUNT(*) " + predicate,
                Long.class,
                parameters.toArray());
        List<Object> pageParameters = new ArrayList<>(parameters);
        pageParameters.add(pageSize);
        pageParameters.add((long) (page - 1) * pageSize);
        List<DirectoryUserRow> rows = jdbc.query("""
                        SELECT u.id, u.organization_user_uid,
                               u.phone_e164, u.phone_bound_at,
                               u.nickname, u.avatar_url,
                               u.status, u.auth_version,
                               u.lock_version, u.registered_at,
                               u.registered_via_asset_id IS NOT NULL
                                   AS registration_source_present,
                               EXISTS (
                                   SELECT 1
                                   FROM iam_organization_user_capability c
                                   WHERE c.tenant_id = u.tenant_id
                                     AND c.organization_id =
                                         u.organization_id
                                     AND c.organization_user_id = u.id
                                     AND c.capability_code =
                                         'CLEAN_OPERATION'
                                     AND c.enabled = 1
                               ) AS clean_operation_enabled
                        """
                        + predicate
                        + " ORDER BY u.registered_at DESC, u.id DESC"
                        + " LIMIT ? OFFSET ?",
                (rs, ignored) -> directoryUserRow(rs),
                pageParameters.toArray());
        List<OrganizationUserView> items =
                organizationUserViews(scope, rows);
        return new PageData<>(
                List.copyOf(items), page, pageSize, total);
    }

    @Transactional(readOnly = true)
    public OrganizationUserView organizationUser(
            String tenantCode,
            String organizationCode,
            UUID organizationUserUid) {
        Scope scope = scope(tenantCode, organizationCode, false);
        requireAnyCapability(
                scope, "user.read", "user.freeze", "cleaner.manage");
        return organizationUserViewWithSource(
                scope,
                directoryUserByUid(
                        scope, organizationUserUid, false));
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public OrganizationUserView freezeOrganizationUser(
            UUID operationUid,
            String tenantCode,
            String organizationCode,
            UUID organizationUserUid,
            AccountVersionCommand request) {
        return mutateOrganizationUser(
                operationUid,
                tenantCode,
                organizationCode,
                organizationUserUid,
                request,
                OrganizationUserMutation.FREEZE);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public OrganizationUserView restoreOrganizationUser(
            UUID operationUid,
            String tenantCode,
            String organizationCode,
            UUID organizationUserUid,
            AccountVersionCommand request) {
        return mutateOrganizationUser(
                operationUid,
                tenantCode,
                organizationCode,
                organizationUserUid,
                request,
                OrganizationUserMutation.RESTORE);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public OrganizationUserView grantCleanOperation(
            UUID operationUid,
            String tenantCode,
            String organizationCode,
            UUID organizationUserUid,
            AccountVersionCommand request) {
        return mutateOrganizationUser(
                operationUid,
                tenantCode,
                organizationCode,
                organizationUserUid,
                request,
                OrganizationUserMutation.GRANT_CLEAN_OPERATION);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public OrganizationUserView revokeCleanOperation(
            UUID operationUid,
            String tenantCode,
            String organizationCode,
            UUID organizationUserUid,
            AccountVersionCommand request) {
        return mutateOrganizationUser(
                operationUid,
                tenantCode,
                organizationCode,
                organizationUserUid,
                request,
                OrganizationUserMutation.REVOKE_CLEAN_OPERATION);
    }

    @Transactional(readOnly = true)
    public StaffMiniappBindingLookupView currentStaffBinding(
            String tenantCode,
            String organizationCode,
            UUID staffUid) {
        Scope scope = scope(tenantCode, organizationCode, false);
        requireCapability(scope, "staff.bind");
        StaffRow staff = staff(scope.tenantId(), staffUid, false);
        BindingRow binding = activeStaffBinding(
                scope.miniappId(), staff.id(), false);
        if (binding == null) {
            return new StaffMiniappBindingLookupView(null);
        }
        UserRow user = user(scope, binding.userId(), false);
        boolean mayReadUser = allowed(scope, "user.read");
        return new StaffMiniappBindingLookupView(
                new StaffCurrentMiniappBinding(
                        binding.uid(),
                        user.uid(),
                        binding.version(),
                        mayReadUser ? displayName(user.nickname()) : null,
                        mayReadUser ? user.phoneE164() : null,
                        mayReadUser ? user.phoneE164() : null));
    }

    @Transactional(readOnly = true)
    public String organizationCodeForCurrentTenantBinding(UUID bindingUid) {
        TargetWebActor actor = TargetWebActorContext.required();
        if (actor.platform() || actor.tenantId() == null) {
            throw notFound();
        }
        return jdbc.query("""
                        SELECT o.organization_code
                        FROM iam_staff_miniapp_binding b
                        JOIN iam_organization o
                          ON o.tenant_id = b.tenant_id
                         AND o.id = b.organization_id
                        WHERE b.tenant_id = ?
                          AND b.binding_uid = ?
                        """,
                (rs, ignored) -> rs.getString("organization_code"),
                actor.tenantId(),
                bindingUid.toString()).stream()
                .findFirst()
                .orElseThrow(TargetOrganizationUserBindingService::notFound);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public StaffMiniappBindingView setBinding(
            UUID operationUid,
            String tenantCode,
            String organizationCode,
            UUID staffUid,
            SetStaffMiniappBindingRequest request) {
        String target = tenantCode + "|" + organizationCode
                + "|staff:" + staffUid
                + "|user:" + request.organizationUserUid();
        return command(
                operationUid,
                "identity.staff-miniapp-binding.set",
                target,
                request,
                () -> replayCurrentBinding(
                        tenantCode,
                        organizationCode,
                        staffUid,
                        request.organizationUserUid()),
                () -> {
                    Scope scope = scope(tenantCode, organizationCode, true);
                    requireCapability(scope, "staff.bind");
                    StaffRow staff = staff(
                            scope.tenantId(), staffUid, true);
                    UserRow user = userByUid(
                            scope, request.organizationUserUid(), true);
                    if (!staff.enabled()) {
                        throw unprocessable(
                                "IDENTITY.STAFF_DISABLED",
                                "工作人员账号已停用");
                    }
                    if (user.phoneE164() == null
                            || user.phoneBoundAt() == null) {
                        throw unprocessable(
                                "IDENTITY.PHONE_BINDING_REQUIRED",
                                "机构用户尚未绑定手机号");
                    }
                    if (!hasManagementPath(scope, staff)) {
                        throw unprocessable(
                                "IDENTITY.STAFF_MANAGEMENT_PATH_REQUIRED",
                                "工作人员当前不具备该机构管理访问路径");
                    }

                    ActiveBindings activeBindings =
                            lockActiveBindingsInStableOrder(
                                    scope.miniappId(),
                                    staff.id(),
                                    user.id());
                    BindingRow staffBinding =
                            activeBindings.staffBinding();
                    BindingRow userBinding =
                            activeBindings.userBinding();
                    requireSnapshot(
                            request.expectedStaffBinding(), staffBinding);
                    requireSnapshot(
                            request.expectedOrganizationUserBinding(),
                            userBinding);
                    if (staffBinding != null
                            && userBinding != null
                            && staffBinding.id() == userBinding.id()) {
                        return result(
                                bindingView(staffBinding),
                                scope,
                                "STAFF_MINIAPP_BINDING",
                                staffBinding.uid().toString(),
                                bindingView(staffBinding),
                                bindingView(staffBinding),
                                request.reason());
                    }

                    Map<Long, BindingRow> conflicts = new LinkedHashMap<>();
                    if (staffBinding != null) {
                        conflicts.put(staffBinding.id(), staffBinding);
                    }
                    if (userBinding != null) {
                        conflicts.put(userBinding.id(), userBinding);
                    }
                    conflicts.values().forEach(binding ->
                            revokeBinding(
                                    binding,
                                    request.reason() == null
                                            ? "REBOUND"
                                            : request.reason()));

                    UUID bindingUid = UUID.randomUUID();
                    try {
                        jdbc.update("""
                                        INSERT INTO iam_staff_miniapp_binding (
                                            binding_uid, tenant_id,
                                            organization_id,
                                            miniapp_channel_id,
                                            organization_user_id,
                                            staff_account_id, status,
                                            bound_at, revoked_at,
                                            revocation_reason,
                                            lock_version, created_at
                                        ) VALUES (
                                            ?, ?, ?, ?, ?, ?, 'ACTIVE',
                                            UTC_TIMESTAMP(3), NULL, NULL,
                                            0, UTC_TIMESTAMP(3)
                                        )
                                        """,
                                bindingUid.toString(),
                                scope.tenantId(),
                                scope.organizationId(),
                                scope.miniappId(),
                                user.id(),
                                staff.id());
                    } catch (DataIntegrityViolationException exception) {
                        throw versionConflict();
                    }
                    BindingRow created = bindingByUid(
                            scope, bindingUid, false);
                    sessionRepository.revokeOrganizationUserSessions(
                            scope.tenantId(),
                            scope.organizationId(),
                            user.id(),
                            "STAFF_MINIAPP_BINDING_CHANGED");
                    return result(
                            bindingView(created),
                            scope,
                            "STAFF_MINIAPP_BINDING",
                            bindingUid.toString(),
                            staffBinding == null
                                    ? null : bindingView(staffBinding),
                            bindingView(created),
                            request.reason());
                });
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public StaffMiniappBindingView revokeBinding(
            UUID operationUid,
            String tenantCode,
            String organizationCode,
            UUID bindingUid,
            VersionCommand request) {
        String target = tenantCode + "|" + organizationCode
                + "|binding:" + bindingUid;
        return command(
                operationUid,
                "identity.staff-miniapp-binding.revoke",
                target,
                request,
                () -> {
                    Scope scope = scope(
                            tenantCode, organizationCode, false);
                    requireCapability(scope, "staff.bind");
                    return bindingView(bindingByUid(
                            scope, bindingUid, false));
                },
                () -> {
                    Scope scope = scope(tenantCode, organizationCode, true);
                    requireCapability(scope, "staff.bind");
                    BindingRow binding = bindingByUid(
                            scope, bindingUid, true);
                    if (!"ACTIVE".equals(binding.status())
                            || binding.version()
                            != request.expectedVersion()) {
                        throw versionConflict();
                    }
                    StaffMiniappBindingView before =
                            bindingView(binding);
                    revokeBinding(binding, request.reason());
                    BindingRow revoked = bindingByUid(
                            scope, bindingUid, false);
                    return result(
                            bindingView(revoked),
                            scope,
                            "STAFF_MINIAPP_BINDING",
                            bindingUid.toString(),
                            before,
                            bindingView(revoked),
                            request.reason());
                });
    }

    private void revokeBinding(BindingRow binding, String reason) {
        jdbc.update("""
                        UPDATE iam_staff_miniapp_binding
                        SET status = 'REVOKED',
                            revoked_at = UTC_TIMESTAMP(3),
                            revocation_reason = ?,
                            lock_version = lock_version + 1
                        WHERE id = ?
                          AND status = 'ACTIVE'
                        """,
                blankToNull(reason), binding.id());
        sessionRepository.revokeMiniappBindingSessions(
                binding.id(), "STAFF_MINIAPP_BINDING_REVOKED");
    }

    private StaffMiniappBindingView replayCurrentBinding(
            String tenantCode,
            String organizationCode,
            UUID staffUid,
            UUID expectedUserUid) {
        Scope scope = scope(tenantCode, organizationCode, false);
        requireCapability(scope, "staff.bind");
        StaffRow staff = staff(scope.tenantId(), staffUid, false);
        BindingRow binding = activeStaffBinding(
                scope.miniappId(), staff.id(), false);
        if (binding == null) {
            throw idempotencyConflict();
        }
        UserRow user = user(scope, binding.userId(), false);
        if (!user.uid().equals(expectedUserUid)) {
            throw idempotencyConflict();
        }
        return bindingView(binding);
    }

    private OrganizationUserView mutateOrganizationUser(
            UUID operationUid,
            String tenantCode,
            String organizationCode,
            UUID organizationUserUid,
            AccountVersionCommand request,
            OrganizationUserMutation mutation) {
        if (request == null
                || request.expectedVersion() == null
                || request.expectedAuthVersion() == null) {
            throw invalidRequest();
        }
        String target = tenantCode + "|" + organizationCode
                + "|user:" + organizationUserUid;
        return command(
                operationUid,
                mutation.action(),
                target,
                request,
                () -> replayOrganizationUserMutation(
                        tenantCode,
                        organizationCode,
                        organizationUserUid,
                        request,
                        mutation),
                () -> {
                    Scope scope = scope(
                            tenantCode, organizationCode, true);
                    requireCapability(scope, mutation.requiredCapability());
                    DirectoryUserRow before = directoryUserByUid(
                            scope, organizationUserUid, true);
                    requireUserVersions(before, request);
                    CapabilityRow capability = null;
                    if (mutation.capabilityMutation()) {
                        capability = cleanOperationCapability(
                                scope, before.id(), true);
                    }
                    validateMutationState(before, capability, mutation);
                    applyOrganizationUserMutation(
                            scope, before, capability, mutation);
                    sessionRepository.revokeOrganizationUserSessions(
                            scope.tenantId(),
                            scope.organizationId(),
                            before.id(),
                            mutation.revocationReason());
                    DirectoryUserRow after = directoryUserByUid(
                            scope, organizationUserUid, false);
                    return result(
                            organizationUserViewWithSource(scope, after),
                            scope,
                            "ORGANIZATION_USER",
                            organizationUserUid.toString(),
                            organizationUserAuditSnapshot(before),
                            organizationUserAuditSnapshot(after),
                            request.reason());
                });
    }

    private OrganizationUserView replayOrganizationUserMutation(
            String tenantCode,
            String organizationCode,
            UUID organizationUserUid,
            AccountVersionCommand request,
            OrganizationUserMutation mutation) {
        Scope scope = scope(tenantCode, organizationCode, false);
        requireCapability(scope, mutation.requiredCapability());
        DirectoryUserRow current = directoryUserByUid(
                scope, organizationUserUid, false);
        boolean capabilityEnabled = current.cleanOperationEnabled();
        boolean desiredState = switch (mutation) {
            case FREEZE -> "FROZEN".equals(current.status());
            case RESTORE -> "ACTIVE".equals(current.status());
            case GRANT_CLEAN_OPERATION -> capabilityEnabled;
            case REVOKE_CLEAN_OPERATION -> !capabilityEnabled;
        };
        if (!desiredState
                || current.version() != request.expectedVersion() + 1
                || current.authVersion()
                != request.expectedAuthVersion() + 1) {
            throw idempotencyConflict();
        }
        return organizationUserViewWithSource(scope, current);
    }

    private static void requireUserVersions(
            DirectoryUserRow user,
            AccountVersionCommand request) {
        if (user.version() != request.expectedVersion()
                || user.authVersion() != request.expectedAuthVersion()) {
            throw userVersionConflict(user);
        }
    }

    private static void validateMutationState(
            DirectoryUserRow user,
            CapabilityRow capability,
            OrganizationUserMutation mutation) {
        switch (mutation) {
            case FREEZE -> {
                if ("FROZEN".equals(user.status())) {
                    throw unprocessable(
                            "IDENTITY.ORGANIZATION_USER_ALREADY_FROZEN",
                            "机构用户已经冻结");
                }
            }
            case RESTORE -> {
                if ("ACTIVE".equals(user.status())) {
                    throw unprocessable(
                            "IDENTITY.ORGANIZATION_USER_ALREADY_ACTIVE",
                            "机构用户已经处于正常状态");
                }
            }
            case GRANT_CLEAN_OPERATION -> {
                if (capability != null && capability.enabled()) {
                    throw unprocessable(
                            "IDENTITY.USER_CAPABILITY_ALREADY_GRANTED",
                            "机构用户已经具有清运能力");
                }
            }
            case REVOKE_CLEAN_OPERATION -> {
                if (capability == null || !capability.enabled()) {
                    throw unprocessable(
                            "IDENTITY.USER_CAPABILITY_NOT_ACTIVE",
                            "机构用户当前没有有效清运能力");
                }
            }
        }
    }

    private void applyOrganizationUserMutation(
            Scope scope,
            DirectoryUserRow user,
            CapabilityRow capability,
            OrganizationUserMutation mutation) {
        switch (mutation) {
            case FREEZE -> jdbc.update("""
                            UPDATE iam_organization_user
                            SET status = 'FROZEN',
                                frozen_at = UTC_TIMESTAMP(3),
                                auth_version = auth_version + 1,
                                lock_version = lock_version + 1,
                                updated_at = UTC_TIMESTAMP(3)
                            WHERE tenant_id = ?
                              AND organization_id = ?
                              AND id = ?
                            """,
                    scope.tenantId(),
                    scope.organizationId(),
                    user.id());
            case RESTORE -> jdbc.update("""
                            UPDATE iam_organization_user
                            SET status = 'ACTIVE',
                                frozen_at = NULL,
                                auth_version = auth_version + 1,
                                lock_version = lock_version + 1,
                                updated_at = UTC_TIMESTAMP(3)
                            WHERE tenant_id = ?
                              AND organization_id = ?
                              AND id = ?
                            """,
                    scope.tenantId(),
                    scope.organizationId(),
                    user.id());
            case GRANT_CLEAN_OPERATION -> {
                if (capability == null) {
                    jdbc.update("""
                                    INSERT INTO
                                        iam_organization_user_capability (
                                            tenant_id, organization_id,
                                            organization_user_id,
                                            capability_code, enabled,
                                            granted_at, revoked_at,
                                            lock_version, updated_at
                                        )
                                    VALUES (?, ?, ?, 'CLEAN_OPERATION', 1,
                                            UTC_TIMESTAMP(3), NULL, 0,
                                            UTC_TIMESTAMP(3))
                                    """,
                            scope.tenantId(),
                            scope.organizationId(),
                            user.id());
                } else {
                    jdbc.update("""
                                    UPDATE iam_organization_user_capability
                                    SET enabled = 1,
                                        granted_at = UTC_TIMESTAMP(3),
                                        revoked_at = NULL,
                                        lock_version = lock_version + 1,
                                        updated_at = UTC_TIMESTAMP(3)
                                    WHERE id = ?
                                    """,
                            capability.id());
                }
                incrementOrganizationUserVersions(scope, user.id());
            }
            case REVOKE_CLEAN_OPERATION -> {
                jdbc.update("""
                                UPDATE iam_organization_user_capability
                                SET enabled = 0,
                                    revoked_at = UTC_TIMESTAMP(3),
                                    lock_version = lock_version + 1,
                                    updated_at = UTC_TIMESTAMP(3)
                                WHERE id = ?
                                """,
                        capability.id());
                incrementOrganizationUserVersions(scope, user.id());
            }
        }
    }

    private void incrementOrganizationUserVersions(
            Scope scope,
            long organizationUserId) {
        jdbc.update("""
                        UPDATE iam_organization_user
                        SET auth_version = auth_version + 1,
                            lock_version = lock_version + 1,
                            updated_at = UTC_TIMESTAMP(3)
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND id = ?
                        """,
                scope.tenantId(),
                scope.organizationId(),
                organizationUserId);
    }

    private <T> T command(
            UUID operationUid,
            String action,
            String target,
            Object request,
            Supplier<T> replayWork,
            Supplier<CommandResult<T>> work) {
        validateOperationUid(operationUid);
        TargetWebAuditRequestContext.describe(action, target);
        TargetWebActor actor = TargetWebActorContext.required();
        lockAndRevalidateActor(actor);
        String fingerprint = fingerprint(actor, action, target, request);
        Optional<SuccessfulAudit> prior =
                auditPort.findSuccessful(operationUid);
        if (prior.isPresent()) {
            SuccessfulAudit audit = prior.get();
            JsonNode summary = readJson(audit.safeChangeSummaryJson());
            boolean sameActor = actor.platform()
                    ? audit.actorKind() == AuditActorKind.PLATFORM_ADMIN
                    && Objects.equals(
                    audit.platformAdminId(), actor.principalId())
                    : audit.actorKind() == AuditActorKind.STAFF_ACCOUNT
                    && Objects.equals(
                    audit.staffAccountId(), actor.principalId());
            if (!sameActor
                    || !action.equals(audit.actionCode())
                    || !fingerprint.equals(
                    summary.path("fingerprint").asText())) {
                throw idempotencyConflict();
            }
            return replayWork.get();
        }
        CommandResult<T> result = work.get();
        String summary = writeJson(Map.of(
                "fingerprint", fingerprint,
                "before", result.before() == null
                        ? Map.of() : result.before(),
                "after", result.after() == null
                        ? Map.of() : result.after(),
                "metadata", Map.of(
                        "reasonPresent",
                        result.reason() != null
                                && !result.reason().isBlank())));
        auditPort.append(new AuditEntry(
                UUID.randomUUID(),
                UUID.randomUUID(),
                operationUid,
                AuditScopeKind.ORGANIZATION,
                result.scope().tenantId(),
                result.scope().organizationId(),
                actor.platform()
                        ? AuditActorKind.PLATFORM_ADMIN
                        : AuditActorKind.STAFF_ACCOUNT,
                actor.platform() ? actor.principalId() : null,
                actor.platform() ? null : actor.principalId(),
                null,
                null,
                actor.displayName(),
                action,
                result.targetType(),
                result.targetStableKey(),
                "WEB",
                "SUCCEEDED",
                actor.sessionUid(),
                blankToNull(result.reason()),
                summary,
                Instant.now()));
        return result.response();
    }

    private void lockAndRevalidateActor(TargetWebActor actor) {
        Boolean valid;
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
                    .findFirst().orElse(false);
        } else {
            valid = jdbc.query("""
                            SELECT s.enabled, s.auth_version,
                                   t.status AS tenant_status
                            FROM iam_staff_account s
                            JOIN iam_tenant t ON t.id = s.tenant_id
                            WHERE s.id = ?
                              AND s.tenant_id = ?
                            FOR UPDATE
                            """,
                    (rs, ignored) -> rs.getBoolean("enabled")
                            && rs.getLong("auth_version")
                            == actor.authVersion()
                            && "ENABLED".equals(
                            rs.getString("tenant_status")),
                    actor.principalId(),
                    actor.tenantId()).stream()
                    .findFirst().orElse(false);
        }
        if (!valid) {
            throw new TargetApiException(
                    401,
                    "AUTH.SESSION_INVALID",
                    "登录状态已经变化，请重新登录");
        }
    }

    private Scope scope(
            String tenantCode,
            String organizationCode,
            boolean forUpdate) {
        TargetWebActor actor = TargetWebActorContext.required();
        String effectiveTenant = actor.platform()
                ? normalizeCode(tenantCode)
                : actor.tenantCode();
        if (!actor.platform()
                && tenantCode != null
                && !effectiveTenant.equals(normalizeCode(tenantCode))) {
            throw notFound();
        }
        return jdbc.query("""
                        SELECT t.id AS tenant_id, t.tenant_code,
                               t.status AS tenant_status,
                               o.id AS organization_id,
                               o.organization_code,
                               o.status AS organization_status,
                               m.id AS miniapp_id
                        FROM iam_tenant t
                        JOIN iam_organization o
                          ON o.tenant_id = t.id
                        JOIN iam_organization_miniapp_binding ob
                          ON ob.tenant_id = t.id
                         AND ob.organization_id = o.id
                         AND ob.status = 'ACTIVE'
                        JOIN iam_miniapp_channel m
                          ON m.id = ob.miniapp_channel_id
                        WHERE t.tenant_code = ?
                          AND o.organization_code = ?
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> new Scope(
                        rs.getLong("tenant_id"),
                        rs.getString("tenant_code"),
                        rs.getString("tenant_status"),
                        rs.getLong("organization_id"),
                        rs.getString("organization_code"),
                        rs.getString("organization_status"),
                        rs.getLong("miniapp_id")),
                effectiveTenant,
                normalizeCode(organizationCode)).stream()
                .findFirst()
                .orElseThrow(TargetOrganizationUserBindingService::notFound);
    }

    private void requireCapability(Scope scope, String capability) {
        if (!allowed(scope, capability)) {
            TargetWebActor actor = TargetWebActorContext.required();
            if (!actor.platform()
                    && actor.organization(scope.organizationCode()) == null
                    && !actor.hasTenantCapability(capability)) {
                throw notFound();
            }
            throw new TargetApiException(
                    403,
                    "AUTH.FORBIDDEN",
                    "当前账号无权执行该操作");
        }
    }

    private void requireAnyCapability(
            Scope scope,
            String... capabilities) {
        for (String capability : capabilities) {
            if (allowed(scope, capability)) {
                return;
            }
        }
        TargetWebActor actor = TargetWebActorContext.required();
        if (!actor.platform()
                && actor.organization(scope.organizationCode()) == null
                && java.util.Arrays.stream(capabilities)
                .noneMatch(actor::hasTenantCapability)) {
            throw notFound();
        }
        throw new TargetApiException(
                403,
                "AUTH.FORBIDDEN",
                "当前账号无权读取该机构用户");
    }

    private boolean allowed(Scope scope, String capability) {
        TargetWebActor actor = TargetWebActorContext.required();
        return actor.platform()
                || actor.hasOrganizationCapability(
                scope.organizationCode(), capability);
    }

    private boolean hasManagementPath(Scope scope, StaffRow staff) {
        if ("TENANT_PRINCIPAL".equals(staff.accountKind())) {
            return true;
        }
        int count = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM (
                            SELECT m.id
                            FROM iam_organization_staff_membership m
                            WHERE m.tenant_id = ?
                              AND m.organization_id = ?
                              AND m.staff_account_id = ?
                              AND m.enabled = 1
                              AND m.is_manager = 1
                            UNION ALL
                            SELECT g.id
                            FROM iam_staff_permission_grant g
                            JOIN iam_permission_definition p
                              ON p.id = g.permission_definition_id
                             AND p.scope_kind = g.scope_kind
                            WHERE g.tenant_id = ?
                              AND g.staff_account_id = ?
                              AND g.revoked_at IS NULL
                              AND p.enabled = 1
                              AND (
                                  (g.scope_kind = 'TENANT'
                                   AND g.organization_id IS NULL)
                                  OR
                                  (g.scope_kind = 'ORGANIZATION'
                                   AND g.organization_id = ?)
                              )
                        ) access_path
                        """,
                Integer.class,
                scope.tenantId(),
                scope.organizationId(),
                staff.id(),
                scope.tenantId(),
                staff.id(),
                scope.organizationId());
        return count > 0;
    }

    private StaffRow staff(
            long tenantId,
            UUID staffUid,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT id, staff_account_uid, account_kind, enabled
                        FROM iam_staff_account
                        WHERE tenant_id = ?
                          AND staff_account_uid = ?
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> new StaffRow(
                        rs.getLong("id"),
                        UUID.fromString(
                                rs.getString("staff_account_uid")),
                        rs.getString("account_kind"),
                        rs.getBoolean("enabled")),
                tenantId,
                staffUid.toString()).stream()
                .findFirst()
                .orElseThrow(TargetOrganizationUserBindingService::notFound);
    }

    private UserRow userByUid(
            Scope scope,
            UUID userUid,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT id, organization_user_uid, phone_e164,
                               phone_bound_at, nickname, status,
                               registered_at, lock_version
                        FROM iam_organization_user
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND miniapp_channel_id = ?
                          AND organization_user_uid = ?
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> userRow(rs),
                scope.tenantId(),
                scope.organizationId(),
                scope.miniappId(),
                userUid.toString()).stream()
                .findFirst()
                .orElseThrow(TargetOrganizationUserBindingService::notFound);
    }

    private UserRow user(
            Scope scope,
            long userId,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT id, organization_user_uid, phone_e164,
                               phone_bound_at, nickname, status,
                               registered_at, lock_version
                        FROM iam_organization_user
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND miniapp_channel_id = ?
                          AND id = ?
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> userRow(rs),
                scope.tenantId(),
                scope.organizationId(),
                scope.miniappId(),
                userId).stream()
                .findFirst()
                .orElseThrow(TargetOrganizationUserBindingService::notFound);
    }

    private DirectoryUserRow directoryUserByUid(
            Scope scope,
            UUID userUid,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT u.id, u.organization_user_uid,
                               u.phone_e164, u.phone_bound_at,
                               u.nickname, u.avatar_url,
                               u.status, u.auth_version,
                               u.lock_version, u.registered_at,
                               u.registered_via_asset_id IS NOT NULL
                                   AS registration_source_present,
                               EXISTS (
                                   SELECT 1
                                   FROM iam_organization_user_capability c
                                   WHERE c.tenant_id = u.tenant_id
                                     AND c.organization_id =
                                         u.organization_id
                                     AND c.organization_user_id = u.id
                                     AND c.capability_code =
                                         'CLEAN_OPERATION'
                                     AND c.enabled = 1
                               ) AS clean_operation_enabled
                        FROM iam_organization_user u
                        WHERE u.tenant_id = ?
                          AND u.organization_id = ?
                          AND u.miniapp_channel_id = ?
                          AND u.organization_user_uid = ?
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> directoryUserRow(rs),
                scope.tenantId(),
                scope.organizationId(),
                scope.miniappId(),
                userUid.toString()).stream()
                .findFirst()
                .orElseThrow(TargetOrganizationUserBindingService::notFound);
    }

    private CapabilityRow cleanOperationCapability(
            Scope scope,
            long userId,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT id, enabled, lock_version
                        FROM iam_organization_user_capability
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND organization_user_id = ?
                          AND capability_code = 'CLEAN_OPERATION'
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> new CapabilityRow(
                        rs.getLong("id"),
                        rs.getBoolean("enabled"),
                        rs.getLong("lock_version")),
                scope.tenantId(),
                scope.organizationId(),
                userId).stream().findFirst().orElse(null);
    }

    private BindingRow activeStaffBinding(
            long miniappId,
            long staffId,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT id, binding_uid, tenant_id, organization_id,
                               miniapp_channel_id,
                               organization_user_id, staff_account_id,
                               status, bound_at, revoked_at, lock_version,
                               (SELECT organization_user_uid
                                FROM iam_organization_user
                                WHERE id = organization_user_id)
                                   AS organization_user_uid,
                               (SELECT staff_account_uid
                                FROM iam_staff_account
                                WHERE id = staff_account_id
                                  AND tenant_id =
                                      iam_staff_miniapp_binding.tenant_id)
                                   AS staff_account_uid
                        FROM iam_staff_miniapp_binding
                        WHERE miniapp_channel_id = ?
                          AND staff_account_id = ?
                          AND status = 'ACTIVE'
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> bindingRow(rs),
                miniappId,
                staffId).stream().findFirst().orElse(null);
    }

    private BindingRow activeUserBinding(
            long userId,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT id, binding_uid, tenant_id, organization_id,
                               miniapp_channel_id,
                               organization_user_id, staff_account_id,
                               status, bound_at, revoked_at, lock_version,
                               (SELECT organization_user_uid
                                FROM iam_organization_user
                                WHERE id = organization_user_id)
                                   AS organization_user_uid,
                               (SELECT staff_account_uid
                                FROM iam_staff_account
                                WHERE id = staff_account_id
                                  AND tenant_id =
                                      iam_staff_miniapp_binding.tenant_id)
                                   AS staff_account_uid
                        FROM iam_staff_miniapp_binding
                        WHERE organization_user_id = ?
                          AND status = 'ACTIVE'
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> bindingRow(rs),
                userId).stream().findFirst().orElse(null);
    }

    private ActiveBindings lockActiveBindingsInStableOrder(
            long miniappId,
            long staffId,
            long userId) {
        List<Long> ids = jdbc.queryForList("""
                        SELECT id
                        FROM iam_staff_miniapp_binding
                        WHERE status = 'ACTIVE'
                          AND (
                              (
                                  miniapp_channel_id = ?
                                  AND staff_account_id = ?
                              )
                              OR organization_user_id = ?
                          )
                        ORDER BY id
                        """,
                Long.class,
                miniappId,
                staffId,
                userId);
        BindingRow staffBinding = null;
        BindingRow userBinding = null;
        for (Long id : ids.stream().distinct().sorted().toList()) {
            BindingRow binding = bindingById(id, true);
            if (!"ACTIVE".equals(binding.status())) {
                continue;
            }
            if (binding.miniappId() == miniappId
                    && binding.staffId() == staffId) {
                staffBinding = binding;
            }
            if (binding.userId() == userId) {
                userBinding = binding;
            }
        }
        return new ActiveBindings(staffBinding, userBinding);
    }

    private BindingRow bindingById(long bindingId, boolean forUpdate) {
        return jdbc.query("""
                        SELECT id, binding_uid, tenant_id, organization_id,
                               miniapp_channel_id,
                               organization_user_id, staff_account_id,
                               status, bound_at, revoked_at, lock_version,
                               (SELECT organization_user_uid
                                FROM iam_organization_user
                                WHERE id = organization_user_id)
                                   AS organization_user_uid,
                               (SELECT staff_account_uid
                                FROM iam_staff_account
                                WHERE id = staff_account_id
                                  AND tenant_id =
                                      iam_staff_miniapp_binding.tenant_id)
                                   AS staff_account_uid
                        FROM iam_staff_miniapp_binding
                        WHERE id = ?
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> bindingRow(rs),
                bindingId).stream()
                .findFirst()
                .orElseThrow(TargetOrganizationUserBindingService::versionConflict);
    }

    private BindingRow bindingByUid(
            Scope scope,
            UUID bindingUid,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT id, binding_uid, tenant_id, organization_id,
                               miniapp_channel_id,
                               organization_user_id, staff_account_id,
                               status, bound_at, revoked_at, lock_version,
                               (SELECT organization_user_uid
                                FROM iam_organization_user
                                WHERE id = organization_user_id)
                                   AS organization_user_uid,
                               (SELECT staff_account_uid
                                FROM iam_staff_account
                                WHERE id = staff_account_id
                                  AND tenant_id =
                                      iam_staff_miniapp_binding.tenant_id)
                                   AS staff_account_uid
                        FROM iam_staff_miniapp_binding
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND miniapp_channel_id = ?
                          AND binding_uid = ?
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> bindingRow(rs),
                scope.tenantId(),
                scope.organizationId(),
                scope.miniappId(),
                bindingUid.toString()).stream()
                .findFirst()
                .orElseThrow(TargetOrganizationUserBindingService::notFound);
    }

    private static void requireSnapshot(
            BindingSnapshot expected,
            BindingRow actual) {
        boolean matches = expected == null
                ? actual == null
                : actual != null
                && expected.bindingUid().equals(actual.uid())
                && expected.version() == actual.version();
        if (!matches) {
            throw versionConflict();
        }
    }

    private static OrganizationUserLookupView userLookup(
            UserRow user,
            BindingRow binding) {
        OrganizationUserCurrentMiniappBinding current = binding == null
                ? null
                : new OrganizationUserCurrentMiniappBinding(
                        binding.uid(),
                        binding.staffUid(),
                        binding.version());
        return new OrganizationUserLookupView(
                user.uid(),
                displayName(user.nickname()),
                user.phoneE164(),
                user.phoneE164(),
                user.registeredAt(),
                user.status(),
                current);
    }

    private OrganizationUserView organizationUserViewWithSource(
            Scope scope,
            DirectoryUserRow user) {
        return organizationUserViews(scope, List.of(user)).getFirst();
    }

    private List<OrganizationUserView> organizationUserViews(
            Scope scope,
            List<DirectoryUserRow> users) {
        List<UUID> sourcedUsers = users.stream()
                .filter(DirectoryUserRow::registrationSourcePresent)
                .map(DirectoryUserRow::uid)
                .toList();
        Map<UUID, RegistrationSourceSummary> sources =
                sourceQuery.findSources(new RegistrationSourceQuery(
                        scope.tenantCode(),
                        scope.organizationCode(),
                        sourcedUsers));
        return users.stream()
                .map(user -> organizationUserView(
                        user, sources.get(user.uid())))
                .toList();
    }

    private static OrganizationUserView organizationUserView(
            DirectoryUserRow user,
            RegistrationSourceSummary sourceSummary) {
        if (user.registrationSourcePresent()
                != (sourceSummary != null)) {
            throw new IllegalStateException(
                    "registration source projection is inconsistent");
        }
        OrganizationUserRegistrationSource source =
                sourceSummary == null
                        ? null
                        : new OrganizationUserRegistrationSource(
                        sourceSummary.deviceCode(),
                        sourceSummary.lifecycleStatus());
        return new OrganizationUserView(
                user.uid(),
                displayName(user.nickname()),
                safeAvatarUrl(user.avatarUrl()),
                user.phoneE164(),
                user.phoneE164(),
                user.phoneE164() != null
                        && user.phoneBoundAt() != null,
                user.registeredAt(),
                source,
                user.status(),
                user.cleanOperationEnabled(),
                user.version(),
                user.authVersion());
    }

    private static Map<String, Object> organizationUserAuditSnapshot(
            DirectoryUserRow user) {
        Map<String, Object> snapshot = new LinkedHashMap<>();
        snapshot.put(
                "organizationUserUid",
                user.uid().toString());
        snapshot.put("status", user.status());
        snapshot.put(
                "cleanOperationEnabled",
                user.cleanOperationEnabled());
        snapshot.put("version", user.version());
        snapshot.put("authVersion", user.authVersion());
        return Map.copyOf(snapshot);
    }

    private static StaffMiniappBindingView bindingView(BindingRow binding) {
        return new StaffMiniappBindingView(
                binding.uid(),
                binding.userUid(),
                binding.staffUid(),
                binding.status(),
                binding.version(),
                binding.boundAt(),
                binding.revokedAt());
    }

    private static UserRow userRow(ResultSet rs) throws SQLException {
        return new UserRow(
                rs.getLong("id"),
                UUID.fromString(rs.getString("organization_user_uid")),
                rs.getString("phone_e164"),
                nullableInstant(rs, "phone_bound_at"),
                rs.getString("nickname"),
                rs.getString("status"),
                instant(rs, "registered_at"),
                rs.getLong("lock_version"));
    }

    private static DirectoryUserRow directoryUserRow(ResultSet rs)
            throws SQLException {
        return new DirectoryUserRow(
                rs.getLong("id"),
                UUID.fromString(rs.getString("organization_user_uid")),
                rs.getString("phone_e164"),
                nullableInstant(rs, "phone_bound_at"),
                rs.getString("nickname"),
                rs.getString("avatar_url"),
                rs.getString("status"),
                rs.getLong("auth_version"),
                rs.getLong("lock_version"),
                instant(rs, "registered_at"),
                rs.getBoolean("registration_source_present"),
                rs.getBoolean("clean_operation_enabled"));
    }

    private static BindingRow bindingRow(ResultSet rs) throws SQLException {
        return new BindingRow(
                rs.getLong("id"),
                UUID.fromString(rs.getString("binding_uid")),
                rs.getLong("tenant_id"),
                rs.getLong("organization_id"),
                rs.getLong("miniapp_channel_id"),
                rs.getLong("organization_user_id"),
                rs.getLong("staff_account_id"),
                UUID.fromString(
                        rs.getString("organization_user_uid")),
                UUID.fromString(rs.getString("staff_account_uid")),
                rs.getString("status"),
                instant(rs, "bound_at"),
                nullableInstant(rs, "revoked_at"),
                rs.getLong("lock_version"));
    }

    private static String normalizePhone(String value) {
        if (value == null) {
            throw invalidRequest();
        }
        String phone = value.trim()
                .replace(" ", "")
                .replace("-", "");
        if (phone.matches("^1[0-9]{10}$")) {
            phone = "+86" + phone;
        }
        if (!E164.matcher(phone).matches()) {
            throw invalidRequest();
        }
        return phone;
    }

    private static String normalizeCode(String value) {
        if (value == null || value.isBlank()) {
            throw invalidRequest();
        }
        return value.trim().toLowerCase(Locale.ROOT);
    }

    private static String normalizeUserStatus(String value) {
        String normalized = blankToNull(value);
        if (normalized == null) {
            return null;
        }
        normalized = normalized.toUpperCase(Locale.ROOT);
        if (!"ACTIVE".equals(normalized)
                && !"FROZEN".equals(normalized)) {
            throw invalidRequest();
        }
        return normalized;
    }

    private static String displayName(String nickname) {
        return nickname == null || nickname.isBlank()
                ? "微信用户"
                : nickname;
    }

    private static String safeAvatarUrl(String avatarUrl) {
        String value = blankToNull(avatarUrl);
        if (value == null) {
            return null;
        }
        String lower = value.toLowerCase(Locale.ROOT);
        return lower.startsWith("https://") || lower.startsWith("http://")
                ? value : null;
    }

    private String fingerprint(
            TargetWebActor actor,
            String action,
            String target,
            Object request) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            digest.update(actor.principalUid().toString()
                    .getBytes(StandardCharsets.UTF_8));
            digest.update((byte) 0);
            digest.update(action.getBytes(StandardCharsets.UTF_8));
            digest.update((byte) 0);
            digest.update(target.getBytes(StandardCharsets.UTF_8));
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
                    "safe audit summary cannot be encoded", exception);
        }
    }

    private static void validateOperationUid(UUID value) {
        if (value == null || value.version() != 4 || value.variant() != 2) {
            throw new TargetApiException(
                    400,
                    "COMMON.INVALID_IDEMPOTENCY_KEY",
                    "Idempotency-Key 必须是 UUIDv4");
        }
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

    private static String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value.trim();
    }

    private static <T> CommandResult<T> result(
            T response,
            Scope scope,
            String targetType,
            String targetStableKey,
            Object before,
            Object after,
            String reason) {
        return new CommandResult<>(
                response,
                scope,
                targetType,
                targetStableKey,
                before,
                after,
                reason);
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404, "COMMON.NOT_FOUND", "未找到指定资源");
    }

    private static TargetApiException invalidRequest() {
        return new TargetApiException(
                400, "COMMON.INVALID_REQUEST", "请求字段不符合接口契约");
    }

    private static TargetApiException unprocessable(
            String code,
            String message) {
        return new TargetApiException(422, code, message);
    }

    private static TargetApiException versionConflict() {
        return new TargetApiException(
                409,
                "IDENTITY.MINIAPP_BINDING_VERSION_CONFLICT",
                "小程序绑定状态已经变化，请刷新后重试");
    }

    private static TargetApiException userVersionConflict(
            DirectoryUserRow current) {
        return new TargetApiException(
                409,
                "COMMON.VERSION_CONFLICT",
                "机构用户状态已经变化，请刷新后重试",
                false,
                Map.of(
                        "currentVersion", current.version(),
                        "currentAuthVersion", current.authVersion()));
    }

    private static TargetApiException idempotencyConflict() {
        return new TargetApiException(
                409,
                "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                "相同操作标识已绑定到不同请求");
    }

    private record Scope(
            long tenantId,
            String tenantCode,
            String tenantStatus,
            long organizationId,
            String organizationCode,
            String organizationStatus,
            long miniappId) {
    }

    private record StaffRow(
            long id,
            UUID uid,
            String accountKind,
            boolean enabled) {
    }

    private record UserRow(
            long id,
            UUID uid,
            String phoneE164,
            Instant phoneBoundAt,
            String nickname,
            String status,
            Instant registeredAt,
            long version) {
    }

    private record DirectoryUserRow(
            long id,
            UUID uid,
            String phoneE164,
            Instant phoneBoundAt,
            String nickname,
            String avatarUrl,
            String status,
            long authVersion,
            long version,
            Instant registeredAt,
            boolean registrationSourcePresent,
            boolean cleanOperationEnabled) {
    }

    private record CapabilityRow(
            long id,
            boolean enabled,
            long version) {
    }

    private record BindingRow(
            long id,
            UUID uid,
            long tenantId,
            long organizationId,
            long miniappId,
            long userId,
            long staffId,
            UUID userUid,
            UUID staffUid,
            String status,
            Instant boundAt,
            Instant revokedAt,
            long version) {
    }

    private record ActiveBindings(
            BindingRow staffBinding,
            BindingRow userBinding) {
    }

    private enum OrganizationUserMutation {
        FREEZE(
                "identity.organization-user.freeze",
                "user.freeze",
                "ORGANIZATION_USER_FROZEN",
                false),
        RESTORE(
                "identity.organization-user.restore",
                "user.freeze",
                "ORGANIZATION_USER_RESTORED",
                false),
        GRANT_CLEAN_OPERATION(
                "identity.organization-user.clean-operation.grant",
                "cleaner.manage",
                "CLEAN_OPERATION_CAPABILITY_CHANGED",
                true),
        REVOKE_CLEAN_OPERATION(
                "identity.organization-user.clean-operation.revoke",
                "cleaner.manage",
                "CLEAN_OPERATION_CAPABILITY_CHANGED",
                true);

        private final String action;
        private final String requiredCapability;
        private final String revocationReason;
        private final boolean capabilityMutation;

        OrganizationUserMutation(
                String action,
                String requiredCapability,
                String revocationReason,
                boolean capabilityMutation) {
            this.action = action;
            this.requiredCapability = requiredCapability;
            this.revocationReason = revocationReason;
            this.capabilityMutation = capabilityMutation;
        }

        String action() {
            return action;
        }

        String requiredCapability() {
            return requiredCapability;
        }

        String revocationReason() {
            return revocationReason;
        }

        boolean capabilityMutation() {
            return capabilityMutation;
        }
    }

    private record CommandResult<T>(
            T response,
            Scope scope,
            String targetType,
            String targetStableKey,
            Object before,
            Object after,
            String reason) {
    }
}
