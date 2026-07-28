package org.enveloping.ecobin.identity.application.miniapp;

import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.framework.security.JwtTokenProvider;
import org.enveloping.ecobin.identity.api.command.OrganizationUserRegistrationCommand;
import org.enveloping.ecobin.identity.api.id.OrganizationUid;
import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.enveloping.ecobin.identity.api.id.TenantUid;
import org.enveloping.ecobin.identity.api.port.OrganizationUserRegistrationParticipant;
import org.enveloping.ecobin.identity.api.port.OrganizationUserRegistrationAttributionPort;
import org.enveloping.ecobin.identity.api.port.OrganizationUserRegistrationAttributionPort.RegistrationAttributionQuery;
import org.enveloping.ecobin.identity.api.persistence.OrganizationUserRegistrationAttributionRef;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappLoginService.MiniappConfiguration;
import org.enveloping.ecobin.identity.application.persistence.OrganizationUserWalletOwnerRefFactory;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.web.v1.miniapp.MiniappModels.MiniappSessionCreated;
import org.enveloping.ecobin.identity.web.v1.miniapp.MiniappModels.OrganizationSummary;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;

@Service
public class TargetMiniappLoginTransactionService {

    private static final Set<String> MINIAPP_STAFF_CAPABILITY_ALLOWLIST =
            Set.of("statistics.read", "device.read", "alert.read");

    private final JdbcTemplate jdbc;
    private final OrganizationUserRegistrationAttributionPort deploymentLookup;
    private final OrganizationUserRegistrationParticipant registrationParticipant;
    private final OrganizationUserWalletOwnerRefFactory walletOwnerRefFactory;
    private final JwtTokenProvider tokenProvider;
    private final Duration sessionDuration;

    public TargetMiniappLoginTransactionService(
            JdbcTemplate jdbc,
            OrganizationUserRegistrationAttributionPort deploymentLookup,
            OrganizationUserRegistrationParticipant registrationParticipant,
            OrganizationUserWalletOwnerRefFactory walletOwnerRefFactory,
            JwtTokenProvider tokenProvider,
            @Value("${ecobin.identity.miniapp-session-duration:PT2H}")
            Duration sessionDuration) {
        this.jdbc = jdbc;
        this.deploymentLookup = deploymentLookup;
        this.registrationParticipant = registrationParticipant;
        this.walletOwnerRefFactory = walletOwnerRefFactory;
        this.tokenProvider = tokenProvider;
        this.sessionDuration = sessionDuration;
    }

    @Transactional(readOnly = true)
    public MiniappConfiguration readEnabledConfiguration(String appId) {
        MiniappRow row = configurationByAppId(appId, false);
        requireEnabled(row);
        return new MiniappConfiguration(row.appId(), row.secretReference());
    }

    @Transactional(
            isolation = Isolation.READ_COMMITTED,
            rollbackFor = Exception.class)
    public MiniappSessionCreated completeLogin(
            String appId,
            String openid,
            String deploymentCode) {
        MiniappRow candidate = configurationByAppId(appId, false);
        lockTenant(candidate.tenantId());
        lockOrganization(candidate.tenantId(), candidate.organizationId());
        MiniappRow configuration = configurationByAppId(appId, true);
        requireEnabled(configuration);

        OrganizationUserRegistrationAttributionRef deploymentRef = null;
        if (deploymentCode != null && !deploymentCode.isBlank()) {
            deploymentRef = deploymentLookup.resolve(
                            new RegistrationAttributionQuery(
                                    deploymentCode.trim(),
                                    configuration.tenantCode(),
                                    configuration.organizationCode()))
                    .map(OrganizationUserRegistrationAttributionPort
                            .ResolvedRegistrationAttribution::persistenceRef)
                    .orElseThrow(() -> new TargetApiException(
                            422,
                            "IDENTITY.REGISTRATION_SOURCE_INVALID",
                            "注册来源部署无效或不属于当前机构"));
        }

        OrganizationUserRow user = organizationUser(
                configuration.miniappId(), openid, true);
        boolean newRegistration = user == null;
        if (newRegistration) {
            user = createOrganizationUser(
                    configuration, openid, deploymentRef);
            registrationParticipant.initializeWallet(
                    new OrganizationUserRegistrationCommand(
                            new TenantUid(stableUid(
                                    "tenant", configuration.tenantCode())),
                            new OrganizationUid(stableUid(
                                    "organization",
                                    configuration.tenantCode()
                                            + ":" + configuration.organizationCode())),
                            new OrganizationUserUid(user.uid()),
                            user.registeredAt(),
                            walletOwnerRefFactory.issue(
                                    configuration.tenantId(),
                                    configuration.organizationId(),
                                    user.id())));
        }

        ManagementBinding management = managementBinding(
                configuration, user);
        Instant issuedAt = Instant.now().truncatedTo(ChronoUnit.SECONDS);
        Instant expiresAt = issuedAt.plus(sessionDuration);
        UUID sessionUid = UUID.randomUUID();

        if (management != null) {
            jdbc.update("""
                            INSERT INTO iam_staff_login_session (
                                session_uid, tenant_id, staff_account_id,
                                client_kind, staff_miniapp_binding_id,
                                organization_miniapp_id,
                                active_organization_id,
                                issued_at, expires_at, revoked_at,
                                revocation_reason, login_ip,
                                user_agent_sha256, auth_version_snapshot,
                                created_at
                            ) VALUES (
                                ?, ?, ?, 'MINIAPP_MANAGEMENT', ?, ?, ?,
                                ?, ?, NULL, NULL, NULL, NULL, ?, ?
                            )
                            """,
                    sessionUid.toString(),
                    configuration.tenantId(),
                    management.staffId(),
                    management.bindingId(),
                    configuration.miniappId(),
                    configuration.organizationId(),
                    timestamp(issuedAt),
                    timestamp(expiresAt),
                    management.staffAuthVersion(),
                    timestamp(issuedAt));
            String token = tokenProvider.generateTargetMiniappToken(
                    management.staffUid(),
                    sessionUid,
                    TrustedAudience.MINIAPP_STAFF,
                    issuedAt,
                    expiresAt);
            return created(
                    token,
                    "miniapp-staff",
                    "MANAGEMENT",
                    expiresAt,
                    configuration,
                    management.staffUid(),
                    management.displayName(),
                    management.capabilities(),
                    true,
                    newRegistration);
        }

        if (!"ACTIVE".equals(user.status())) {
            throw new TargetApiException(
                    403,
                    "IDENTITY.ORGANIZATION_USER_FROZEN",
                    "机构用户当前已冻结");
        }
        boolean cleaner = hasCleanerCapability(
                configuration.tenantId(),
                configuration.organizationId(),
                user.id());
        String entryMode = cleaner ? "CLEANING" : "USER";
        List<String> capabilities = cleaner
                ? List.of("clean.operation")
                : List.of();
        jdbc.update("""
                        INSERT INTO iam_organization_user_session (
                            session_uid, tenant_id, organization_id,
                            organization_miniapp_id, organization_user_id,
                            issued_at, expires_at, revoked_at,
                            revocation_reason, login_ip,
                            user_agent_sha256, auth_version_snapshot,
                            created_at
                        ) VALUES (
                            ?, ?, ?, ?, ?,
                            ?, ?, NULL, NULL, NULL, NULL, ?, ?
                        )
                        """,
                sessionUid.toString(),
                configuration.tenantId(),
                configuration.organizationId(),
                configuration.miniappId(),
                user.id(),
                timestamp(issuedAt),
                timestamp(expiresAt),
                user.authVersion(),
                timestamp(issuedAt));
        String token = tokenProvider.generateTargetMiniappToken(
                user.uid(),
                sessionUid,
                TrustedAudience.MINIAPP,
                issuedAt,
                expiresAt);
        return created(
                token,
                "miniapp",
                entryMode,
                expiresAt,
                configuration,
                user.uid(),
                displayName(user.nickname()),
                capabilities,
                user.phoneBoundAt() != null,
                newRegistration);
    }

    private OrganizationUserRow createOrganizationUser(
            MiniappRow configuration,
            String openid,
            OrganizationUserRegistrationAttributionRef deploymentRef) {
        long deploymentId = 0;
        if (deploymentRef != null) {
            long[] keys = new long[3];
            deploymentRef.writeForeignKeyTo((tenant, organization, deployment) -> {
                keys[0] = tenant;
                keys[1] = organization;
                keys[2] = deployment;
            });
            if (keys[0] != configuration.tenantId()
                    || keys[1] != configuration.organizationId()) {
                throw new IllegalStateException(
                        "registration deployment scope mismatch");
            }
            deploymentId = keys[2];
        }
        UUID uid = UUID.randomUUID();
        Instant registeredAt =
                Instant.now().truncatedTo(ChronoUnit.MILLIS);
        jdbc.update("""
                        INSERT INTO iam_organization_user (
                            organization_user_uid,
                            tenant_id, organization_id,
                            organization_miniapp_id, openid,
                            phone_e164, phone_bound_at,
                            nickname, avatar_url, status,
                            auth_version, lock_version,
                            registered_at,
                            registered_via_deployment_id,
                            frozen_at, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, ?, ?,
                            NULL, NULL, '微信用户', NULL, 'ACTIVE',
                            0, 0, ?, ?, NULL, ?, ?
                        )
                        """,
                uid.toString(),
                configuration.tenantId(),
                configuration.organizationId(),
                configuration.miniappId(),
                openid,
                timestamp(registeredAt),
                deploymentId == 0 ? null : deploymentId,
                timestamp(registeredAt),
                timestamp(registeredAt));
        return organizationUserByUid(uid, true);
    }

    private ManagementBinding managementBinding(
            MiniappRow configuration,
            OrganizationUserRow user) {
        BindingRow binding = jdbc.query("""
                        SELECT id, staff_account_id
                        FROM iam_staff_miniapp_binding
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND organization_miniapp_id = ?
                          AND organization_user_id = ?
                          AND status = 'ACTIVE'
                        FOR UPDATE
                        """,
                (rs, ignored) -> new BindingRow(
                        rs.getLong("id"),
                        rs.getLong("staff_account_id")),
                configuration.tenantId(),
                configuration.organizationId(),
                configuration.miniappId(),
                user.id()).stream().findFirst().orElse(null);
        if (binding == null) {
            return null;
        }
        StaffRow staff = staff(
                configuration.tenantId(),
                binding.staffId(),
                true);
        if (staff == null || !staff.enabled()) {
            return null;
        }
        boolean tenantPrincipal =
                "TENANT_PRINCIPAL".equals(staff.accountKind());
        boolean manager = enabledManagerMembership(
                configuration.tenantId(),
                configuration.organizationId(),
                staff.id());
        Set<String> grants = effectiveManagementGrants(
                configuration.tenantId(),
                configuration.organizationId(),
                staff.id());
        if (!tenantPrincipal && !manager && grants.isEmpty()) {
            return null;
        }
        List<String> capabilities;
        if (tenantPrincipal || manager) {
            capabilities = MINIAPP_STAFF_CAPABILITY_ALLOWLIST.stream()
                    .sorted()
                    .toList();
        } else {
            capabilities = grants.stream()
                    .filter(MINIAPP_STAFF_CAPABILITY_ALLOWLIST::contains)
                    .sorted()
                    .toList();
        }
        return new ManagementBinding(
                binding.id(),
                staff.id(),
                staff.uid(),
                staff.displayName(),
                staff.authVersion(),
                capabilities);
    }

    private Set<String> effectiveManagementGrants(
            long tenantId,
            long organizationId,
            long staffId) {
        return new LinkedHashSet<>(jdbc.queryForList("""
                        SELECT DISTINCT p.permission_code
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
                        """,
                String.class,
                tenantId,
                staffId,
                organizationId));
    }

    private boolean enabledManagerMembership(
            long tenantId,
            long organizationId,
            long staffId) {
        return Boolean.TRUE.equals(jdbc.query("""
                        SELECT is_manager
                        FROM iam_organization_staff_membership
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND staff_account_id = ?
                          AND enabled = 1
                        """,
                (rs, ignored) -> rs.getBoolean("is_manager"),
                tenantId, organizationId, staffId)
                .stream().findFirst().orElse(false));
    }

    private boolean hasCleanerCapability(
            long tenantId,
            long organizationId,
            long userId) {
        return Boolean.TRUE.equals(jdbc.query("""
                        SELECT enabled
                        FROM iam_organization_user_capability
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND organization_user_id = ?
                          AND capability_code = 'CLEAN_OPERATION'
                        """,
                (rs, ignored) -> rs.getBoolean("enabled"),
                tenantId, organizationId, userId)
                .stream().findFirst().orElse(false));
    }

    private MiniappRow configurationByAppId(
            String appId,
            boolean forUpdate) {
        String normalized = appId == null ? "" : appId.trim();
        return jdbc.query("""
                        SELECT m.id AS miniapp_id, m.appid, m.secret_ref,
                               m.login_enabled, m.activated_at,
                               t.id AS tenant_id, t.tenant_code,
                               t.status AS tenant_status,
                               o.id AS organization_id,
                               o.organization_code,
                               o.organization_name,
                               o.status AS organization_status
                        FROM iam_organization_miniapp m
                        JOIN iam_tenant t ON t.id = m.tenant_id
                        JOIN iam_organization o
                          ON o.tenant_id = m.tenant_id
                         AND o.id = m.organization_id
                        WHERE m.appid = ?
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> miniappRow(rs),
                normalized).stream().findFirst().orElseThrow(() ->
                new TargetApiException(
                        403,
                        "IDENTITY.MINIAPP_LOGIN_DISABLED",
                        "当前小程序登录入口不可用"));
    }

    private void lockTenant(long tenantId) {
        jdbc.queryForObject("""
                        SELECT id
                        FROM iam_tenant
                        WHERE id = ?
                        FOR UPDATE
                        """,
                Long.class, tenantId);
    }

    private void lockOrganization(long tenantId, long organizationId) {
        jdbc.queryForObject("""
                        SELECT id
                        FROM iam_organization
                        WHERE tenant_id = ?
                          AND id = ?
                        FOR UPDATE
                        """,
                Long.class, tenantId, organizationId);
    }

    private static void requireEnabled(MiniappRow row) {
        if (!"ENABLED".equals(row.tenantStatus())
                || !"ENABLED".equals(row.organizationStatus())
                || !row.loginEnabled()
                || row.activatedAt() == null) {
            throw new TargetApiException(
                    403,
                    "IDENTITY.MINIAPP_LOGIN_DISABLED",
                    "当前小程序登录入口不可用");
        }
    }

    private OrganizationUserRow organizationUser(
            long miniappId,
            String openid,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT id, organization_user_uid,
                               tenant_id, organization_id,
                               organization_miniapp_id,
                               phone_e164, phone_bound_at,
                               nickname, status, auth_version,
                               registered_at,
                               registered_via_deployment_id
                        FROM iam_organization_user
                        WHERE organization_miniapp_id = ?
                          AND openid = ?
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> organizationUserRow(rs),
                miniappId,
                openid).stream().findFirst().orElse(null);
    }

    private OrganizationUserRow organizationUserByUid(
            UUID uid,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT id, organization_user_uid,
                               tenant_id, organization_id,
                               organization_miniapp_id,
                               phone_e164, phone_bound_at,
                               nickname, status, auth_version,
                               registered_at,
                               registered_via_deployment_id
                        FROM iam_organization_user
                        WHERE organization_user_uid = ?
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> organizationUserRow(rs),
                uid.toString()).stream().findFirst().orElseThrow();
    }

    private StaffRow staff(
            long tenantId,
            long staffId,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT id, staff_account_uid, account_kind,
                               display_name, enabled, auth_version
                        FROM iam_staff_account
                        WHERE tenant_id = ?
                          AND id = ?
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> new StaffRow(
                        rs.getLong("id"),
                        UUID.fromString(
                                rs.getString("staff_account_uid")),
                        rs.getString("account_kind"),
                        rs.getString("display_name"),
                        rs.getBoolean("enabled"),
                        rs.getLong("auth_version")),
                tenantId, staffId).stream().findFirst().orElse(null);
    }

    private static MiniappSessionCreated created(
            String token,
            String audience,
            String entryMode,
            Instant expiresAt,
            MiniappRow configuration,
            UUID subjectUid,
            String displayName,
            List<String> capabilities,
            boolean phoneBound,
            boolean newRegistration) {
        return new MiniappSessionCreated(
                token,
                "Bearer",
                audience,
                entryMode,
                expiresAt,
                new OrganizationSummary(
                        configuration.organizationCode(),
                        configuration.organizationName()),
                subjectUid,
                displayName,
                capabilities,
                phoneBound,
                newRegistration);
    }

    private static String displayName(String nickname) {
        return nickname == null || nickname.isBlank()
                ? "微信用户"
                : nickname;
    }

    private static UUID stableUid(String kind, String value) {
        return UUID.nameUUIDFromBytes(
                ("ecobin:" + kind + ":" + value)
                        .getBytes(StandardCharsets.UTF_8));
    }

    private static LocalDateTime timestamp(Instant value) {
        return LocalDateTime.ofInstant(value, ZoneOffset.UTC);
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

    private static MiniappRow miniappRow(ResultSet rs)
            throws SQLException {
        return new MiniappRow(
                rs.getLong("miniapp_id"),
                rs.getString("appid"),
                rs.getString("secret_ref"),
                rs.getBoolean("login_enabled"),
                nullableInstant(rs, "activated_at"),
                rs.getLong("tenant_id"),
                rs.getString("tenant_code"),
                rs.getString("tenant_status"),
                rs.getLong("organization_id"),
                rs.getString("organization_code"),
                rs.getString("organization_name"),
                rs.getString("organization_status"));
    }

    private static OrganizationUserRow organizationUserRow(ResultSet rs)
            throws SQLException {
        long deploymentId = rs.getLong("registered_via_deployment_id");
        boolean deploymentIdWasNull = rs.wasNull();
        return new OrganizationUserRow(
                rs.getLong("id"),
                UUID.fromString(rs.getString("organization_user_uid")),
                rs.getLong("tenant_id"),
                rs.getLong("organization_id"),
                rs.getLong("organization_miniapp_id"),
                rs.getString("phone_e164"),
                nullableInstant(rs, "phone_bound_at"),
                rs.getString("nickname"),
                rs.getString("status"),
                rs.getLong("auth_version"),
                instant(rs, "registered_at"),
                deploymentIdWasNull ? null : deploymentId);
    }

    private record MiniappRow(
            long miniappId,
            String appId,
            String secretReference,
            boolean loginEnabled,
            Instant activatedAt,
            long tenantId,
            String tenantCode,
            String tenantStatus,
            long organizationId,
            String organizationCode,
            String organizationName,
            String organizationStatus) {
    }

    private record OrganizationUserRow(
            long id,
            UUID uid,
            long tenantId,
            long organizationId,
            long miniappId,
            String phoneE164,
            Instant phoneBoundAt,
            String nickname,
            String status,
            long authVersion,
            Instant registeredAt,
            Long registeredViaDeploymentId) {
    }

    private record BindingRow(long id, long staffId) {
    }

    private record StaffRow(
            long id,
            UUID uid,
            String accountKind,
            String displayName,
            boolean enabled,
            long authVersion) {
    }

    private record ManagementBinding(
            long bindingId,
            long staffId,
            UUID staffUid,
            String displayName,
            long staffAuthVersion,
            List<String> capabilities) {
    }
}
