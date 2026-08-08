package org.enveloping.ecobin.identity.application.miniapp;

import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.framework.security.JwtTokenProvider;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.command.OrganizationUserRegistrationCommand;
import org.enveloping.ecobin.identity.api.id.OrganizationUid;
import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.enveloping.ecobin.identity.api.id.TenantUid;
import org.enveloping.ecobin.identity.api.persistence.OrganizationUserRegistrationAttributionRef;
import org.enveloping.ecobin.identity.api.port.OrganizationUserRegistrationAttributionPort;
import org.enveloping.ecobin.identity.api.port.OrganizationUserRegistrationAttributionPort.RegistrationAttributionQuery;
import org.enveloping.ecobin.identity.api.port.OrganizationUserRegistrationParticipant;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappLoginService.MiniappConfiguration;
import org.enveloping.ecobin.identity.application.persistence.OrganizationUserWalletOwnerRefFactory;
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
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;

/**
 * 共享小程序渠道的登录事务。
 *
 * <p>AppID 只定位微信渠道；只有设备公开码可以为新微信主体选择机构并创建账号。
 * 未携带设备码时，本事务只允许选择已有账号，不会隐式创建主体、账号或钱包。</p>
 */
@Service
public class TargetMiniappLoginTransactionService {

    private static final Set<String> MINIAPP_STAFF_CAPABILITY_ALLOWLIST =
            Set.of("statistics.read", "device.read", "alert.read");

    private final JdbcTemplate jdbc;
    private final OrganizationUserRegistrationAttributionPort assetLookup;
    private final OrganizationUserRegistrationParticipant registrationParticipant;
    private final OrganizationUserWalletOwnerRefFactory walletOwnerRefFactory;
    private final JwtTokenProvider tokenProvider;
    private final Duration sessionDuration;

    public TargetMiniappLoginTransactionService(
            JdbcTemplate jdbc,
            OrganizationUserRegistrationAttributionPort assetLookup,
            OrganizationUserRegistrationParticipant registrationParticipant,
            OrganizationUserWalletOwnerRefFactory walletOwnerRefFactory,
            JwtTokenProvider tokenProvider,
            @Value("${ecobin.identity.miniapp-session-duration:PT2H}")
            Duration sessionDuration) {
        this.jdbc = jdbc;
        this.assetLookup = assetLookup;
        this.registrationParticipant = registrationParticipant;
        this.walletOwnerRefFactory = walletOwnerRefFactory;
        this.tokenProvider = tokenProvider;
        this.sessionDuration = sessionDuration;
    }

    @Transactional(readOnly = true)
    public MiniappConfiguration readEnabledConfiguration(String appId) {
        MiniappChannelRow channel = channelByAppId(appId, false);
        requireEnabled(channel);
        return new MiniappConfiguration(channel.appId(), channel.appSecret());
    }

    @Transactional(
            isolation = Isolation.READ_COMMITTED,
            rollbackFor = Exception.class)
    public MiniappSessionCreated completeLogin(
            String appId,
            String openid,
            String deviceCode) {
        MiniappChannelRow channel = channelByAppId(appId, true);
        requireEnabled(channel);

        LoginSelection selection;
        boolean newRegistration = false;
        if (deviceCode != null && !deviceCode.isBlank()) {
            ResolvedAsset resolved = resolveAsset(
                    deviceCode.trim(), channel.appId());
            lockTenant(resolved.tenantId());
            lockOrganization(resolved.tenantId(), resolved.organizationId());
            OrganizationScope scope = organizationScope(
                    resolved.tenantId(),
                    resolved.organizationId(),
                    channel.id(),
                    true);
            requireAvailable(scope);

            WechatSubjectRow subject = findOrCreateSubject(
                    channel.id(), openid);
            requireSubjectActive(subject);
            OrganizationUserRow user = organizationUser(
                    subject.id(), scope.tenantId(), scope.organizationId(), true);
            if (user == null) {
                user = createOrganizationUser(
                        channel, subject, scope, resolved);
                initializeWallet(scope, user);
                newRegistration = true;
            }
            selection = new LoginSelection(subject, scope, user);
        } else {
            WechatSubjectRow subject = wechatSubject(
                    channel.id(), openid, true);
            if (subject == null) {
                throw new TargetApiException(
                        422,
                        "IDENTITY.DEVICE_REGISTRATION_REQUIRED",
                        "首次使用必须扫描设备二维码");
            }
            requireSubjectActive(subject);
            selection = newestAvailableAccount(channel.id(), subject);
            if (selection == null) {
                throw new TargetApiException(
                        403,
                        "IDENTITY.ORGANIZATION_ACCOUNT_UNAVAILABLE",
                        "当前微信身份没有可用的机构账号");
            }
        }

        if (!"ACTIVE".equals(selection.user().status())) {
            throw new TargetApiException(
                    403,
                    "IDENTITY.ORGANIZATION_USER_FROZEN",
                    "机构用户当前已冻结");
        }
        return issueSession(channel, selection, newRegistration, null);
    }

    MiniappSessionCreated issueSession(
            MiniappChannelRow channel,
            LoginSelection selection,
            boolean newRegistration,
            UUID selectionOperationUid) {
        OrganizationScope scope = selection.scope();
        OrganizationUserRow user = selection.user();
        ManagementBinding management = selectionOperationUid == null
                ? managementBinding(channel.id(), scope, user)
                : null;
        Instant issuedAt = Instant.now().truncatedTo(ChronoUnit.SECONDS);
        Instant expiresAt = issuedAt.plus(sessionDuration);
        UUID sessionUid = UUID.randomUUID();

        if (management != null) {
            jdbc.update("""
                            INSERT INTO iam_staff_login_session (
                                session_uid, tenant_id, staff_account_id,
                                client_kind, staff_miniapp_binding_id,
                                miniapp_channel_id,
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
                    scope.tenantId(),
                    management.staffId(),
                    management.bindingId(),
                    channel.id(),
                    scope.organizationId(),
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
                    scope,
                    selection.subject().uid(),
                    user.uid(),
                    management.displayName(),
                    management.capabilities(),
                    true,
                    newRegistration);
        }

        boolean cleaner = hasCleanerCapability(
                scope.tenantId(), scope.organizationId(), user.id());
        String entryMode = cleaner ? "CLEANING" : "USER";
        List<String> capabilities = cleaner
                ? List.of("clean.operation")
                : List.of();
        jdbc.update("""
                        INSERT INTO iam_organization_user_session (
                            session_uid, tenant_id, organization_id,
                            miniapp_channel_id, wechat_subject_id,
                            organization_user_id, selection_operation_uid,
                            issued_at, expires_at, revoked_at,
                            revocation_reason, login_ip,
                            user_agent_sha256, auth_version_snapshot,
                            created_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, NULL, NULL, NULL, NULL, ?, ?
                        )
                        """,
                sessionUid.toString(),
                scope.tenantId(),
                scope.organizationId(),
                channel.id(),
                selection.subject().id(),
                user.id(),
                selectionOperationUid == null
                        ? null : selectionOperationUid.toString(),
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
                scope,
                selection.subject().uid(),
                user.uid(),
                displayName(user.nickname()),
                capabilities,
                user.phoneBoundAt() != null,
                newRegistration);
    }

    LoginSelection accountSelection(
            long channelId,
            long subjectId,
            UUID organizationUserUid) {
        return jdbc.query("""
                        SELECT u.id, u.organization_user_uid,
                               u.tenant_id, u.organization_id,
                               u.miniapp_channel_id, u.wechat_subject_id,
                               u.phone_e164, u.phone_bound_at,
                               u.nickname, u.status, u.auth_version,
                               u.registered_at, u.registered_via_asset_id,
                               s.wechat_subject_uid, s.status AS subject_status,
                               s.auth_version AS subject_auth_version,
                               t.tenant_code, t.status AS tenant_status,
                               o.organization_code, o.organization_name,
                               o.status AS organization_status,
                               b.status AS binding_status
                        FROM iam_organization_user u
                        JOIN iam_wechat_subject s
                          ON s.miniapp_channel_id = u.miniapp_channel_id
                         AND s.id = u.wechat_subject_id
                        JOIN iam_tenant t ON t.id = u.tenant_id
                        JOIN iam_organization o
                          ON o.tenant_id = u.tenant_id
                         AND o.id = u.organization_id
                        JOIN iam_organization_miniapp_binding b
                          ON b.tenant_id = u.tenant_id
                         AND b.organization_id = u.organization_id
                         AND b.miniapp_channel_id = u.miniapp_channel_id
                        WHERE u.organization_user_uid = ?
                          AND u.miniapp_channel_id = ?
                          AND u.wechat_subject_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> loginSelectionRow(rs),
                organizationUserUid.toString(),
                channelId,
                subjectId).stream().findFirst().orElse(null);
    }

    MiniappChannelRow channelById(long channelId, boolean forUpdate) {
        return jdbc.query("""
                        SELECT id AS miniapp_channel_id, appid, app_secret,
                               login_enabled, activated_at
                        FROM iam_miniapp_channel
                        WHERE id = ?
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> miniappChannelRow(rs),
                channelId).stream().findFirst().orElseThrow(() ->
                new TargetApiException(
                        403,
                        "IDENTITY.MINIAPP_LOGIN_DISABLED",
                        "当前小程序登录入口不可用"));
    }

    private ResolvedAsset resolveAsset(String deviceCode, String appId) {
        var resolved = assetLookup.resolve(
                        new RegistrationAttributionQuery(deviceCode, appId))
                .orElseThrow(() -> new TargetApiException(
                        422,
                        "IDENTITY.DEVICE_ENTRY_INVALID",
                        "设备入口无效或当前不可用于注册"));
        long[] keys = new long[3];
        OrganizationUserRegistrationAttributionRef ref =
                resolved.persistenceRef();
        ref.writeForeignKeyTo((tenant, organization, asset) -> {
            keys[0] = tenant;
            keys[1] = organization;
            keys[2] = asset;
        });
        return new ResolvedAsset(
                keys[0],
                resolved.tenantCode(),
                keys[1],
                resolved.organizationCode(),
                resolved.organizationName(),
                keys[2]);
    }

    private WechatSubjectRow findOrCreateSubject(
            long channelId,
            String openid) {
        jdbc.update("""
                        INSERT INTO iam_wechat_subject (
                            wechat_subject_uid, miniapp_channel_id,
                            openid, status, auth_version, lock_version,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, 'ACTIVE', 0, 0, ?, ?)
                        ON DUPLICATE KEY UPDATE
                            lock_version = lock_version
                        """,
                UUID.randomUUID().toString(),
                channelId,
                openid,
                timestamp(Instant.now().truncatedTo(ChronoUnit.MILLIS)),
                timestamp(Instant.now().truncatedTo(ChronoUnit.MILLIS)));
        return wechatSubject(channelId, openid, true);
    }

    private WechatSubjectRow wechatSubject(
            long channelId,
            String openid,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT id, wechat_subject_uid, status, auth_version
                        FROM iam_wechat_subject
                        WHERE miniapp_channel_id = ?
                          AND openid = ?
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> new WechatSubjectRow(
                        rs.getLong("id"),
                        UUID.fromString(rs.getString("wechat_subject_uid")),
                        rs.getString("status"),
                        rs.getLong("auth_version")),
                channelId,
                openid).stream().findFirst().orElse(null);
    }

    private LoginSelection newestAvailableAccount(
            long channelId,
            WechatSubjectRow subject) {
        return jdbc.query("""
                        SELECT u.id, u.organization_user_uid,
                               u.tenant_id, u.organization_id,
                               u.miniapp_channel_id, u.wechat_subject_id,
                               u.phone_e164, u.phone_bound_at,
                               u.nickname, u.status, u.auth_version,
                               u.registered_at, u.registered_via_asset_id,
                               ? AS wechat_subject_uid,
                               ? AS subject_status,
                               ? AS subject_auth_version,
                               t.tenant_code, t.status AS tenant_status,
                               o.organization_code, o.organization_name,
                               o.status AS organization_status,
                               b.status AS binding_status
                        FROM iam_organization_user u
                        JOIN iam_tenant t ON t.id = u.tenant_id
                        JOIN iam_organization o
                          ON o.tenant_id = u.tenant_id
                         AND o.id = u.organization_id
                        JOIN iam_organization_miniapp_binding b
                          ON b.tenant_id = u.tenant_id
                         AND b.organization_id = u.organization_id
                         AND b.miniapp_channel_id = u.miniapp_channel_id
                        WHERE u.miniapp_channel_id = ?
                          AND u.wechat_subject_id = ?
                          AND u.status = 'ACTIVE'
                          AND t.status = 'ENABLED'
                          AND o.status = 'ENABLED'
                          AND b.status = 'ACTIVE'
                        ORDER BY u.registered_at DESC, u.id DESC
                        LIMIT 1
                        FOR UPDATE
                        """,
                (rs, ignored) -> loginSelectionRow(rs),
                subject.uid().toString(),
                subject.status(),
                subject.authVersion(),
                channelId,
                subject.id()).stream().findFirst().orElse(null);
    }

    private OrganizationUserRow organizationUser(
            long subjectId,
            long tenantId,
            long organizationId,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT id, organization_user_uid,
                               tenant_id, organization_id,
                               miniapp_channel_id, wechat_subject_id,
                               phone_e164, phone_bound_at,
                               nickname, status, auth_version,
                               registered_at, registered_via_asset_id
                        FROM iam_organization_user
                        WHERE wechat_subject_id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> organizationUserRow(rs),
                subjectId,
                tenantId,
                organizationId).stream().findFirst().orElse(null);
    }

    private OrganizationUserRow createOrganizationUser(
            MiniappChannelRow channel,
            WechatSubjectRow subject,
            OrganizationScope scope,
            ResolvedAsset asset) {
        if (asset.tenantId() != scope.tenantId()
                || asset.organizationId() != scope.organizationId()) {
            throw new IllegalStateException(
                    "registration asset scope mismatch");
        }
        UUID uid = UUID.randomUUID();
        Instant registeredAt =
                Instant.now().truncatedTo(ChronoUnit.MILLIS);
        jdbc.update("""
                        INSERT INTO iam_organization_user (
                            organization_user_uid,
                            tenant_id, organization_id,
                            miniapp_channel_id, wechat_subject_id,
                            phone_e164, phone_bound_at,
                            nickname, avatar_url, status,
                            auth_version, lock_version,
                            registered_at, registered_via_asset_id,
                            frozen_at, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, ?, ?,
                            NULL, NULL, '微信用户', NULL, 'ACTIVE',
                            0, 0, ?, ?, NULL, ?, ?
                        )
                        """,
                uid.toString(),
                scope.tenantId(),
                scope.organizationId(),
                channel.id(),
                subject.id(),
                timestamp(registeredAt),
                asset.assetId(),
                timestamp(registeredAt),
                timestamp(registeredAt));
        return organizationUserByUid(uid, true);
    }

    private void initializeWallet(
            OrganizationScope scope,
            OrganizationUserRow user) {
        registrationParticipant.initializeWallet(
                new OrganizationUserRegistrationCommand(
                        new TenantUid(stableUid(
                                "tenant", scope.tenantCode())),
                        new OrganizationUid(stableUid(
                                "organization",
                                scope.tenantCode() + ":"
                                        + scope.organizationCode())),
                        new OrganizationUserUid(user.uid()),
                        user.registeredAt(),
                        walletOwnerRefFactory.issue(
                                scope.tenantId(),
                                scope.organizationId(),
                                user.id())));
    }

    private OrganizationUserRow organizationUserByUid(
            UUID uid,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT id, organization_user_uid,
                               tenant_id, organization_id,
                               miniapp_channel_id, wechat_subject_id,
                               phone_e164, phone_bound_at,
                               nickname, status, auth_version,
                               registered_at, registered_via_asset_id
                        FROM iam_organization_user
                        WHERE organization_user_uid = ?
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> organizationUserRow(rs),
                uid.toString()).stream().findFirst().orElseThrow();
    }

    private OrganizationScope organizationScope(
            long tenantId,
            long organizationId,
            long channelId,
            boolean forUpdate) {
        return jdbc.query("""
                        SELECT t.id AS tenant_id, t.tenant_code,
                               t.status AS tenant_status,
                               o.id AS organization_id,
                               o.organization_code, o.organization_name,
                               o.status AS organization_status,
                               b.status AS binding_status
                        FROM iam_tenant t
                        JOIN iam_organization o
                          ON o.tenant_id = t.id
                         AND o.id = ?
                        JOIN iam_organization_miniapp_binding b
                          ON b.tenant_id = t.id
                         AND b.organization_id = o.id
                         AND b.miniapp_channel_id = ?
                        WHERE t.id = ?
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> organizationScopeRow(rs),
                organizationId,
                channelId,
                tenantId).stream().findFirst().orElseThrow(() ->
                new TargetApiException(
                        422,
                        "IDENTITY.DEVICE_ENTRY_INVALID",
                        "设备入口无效或当前不可用于注册"));
    }

    private ManagementBinding managementBinding(
            long channelId,
            OrganizationScope scope,
            OrganizationUserRow user) {
        BindingRow binding = jdbc.query("""
                        SELECT id, staff_account_id
                        FROM iam_staff_miniapp_binding
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND miniapp_channel_id = ?
                          AND organization_user_id = ?
                          AND status = 'ACTIVE'
                        FOR UPDATE
                        """,
                (rs, ignored) -> new BindingRow(
                        rs.getLong("id"),
                        rs.getLong("staff_account_id")),
                scope.tenantId(),
                scope.organizationId(),
                channelId,
                user.id()).stream().findFirst().orElse(null);
        if (binding == null) {
            return null;
        }
        StaffRow staff = staff(scope.tenantId(), binding.staffId(), true);
        if (staff == null || !staff.enabled()) {
            return null;
        }
        boolean tenantPrincipal =
                "TENANT_PRINCIPAL".equals(staff.accountKind());
        boolean manager = enabledManagerMembership(
                scope.tenantId(), scope.organizationId(), staff.id());
        Set<String> grants = effectiveManagementGrants(
                scope.tenantId(), scope.organizationId(), staff.id());
        if (!tenantPrincipal && !manager && grants.isEmpty()) {
            return null;
        }
        List<String> capabilities = tenantPrincipal || manager
                ? MINIAPP_STAFF_CAPABILITY_ALLOWLIST.stream().sorted().toList()
                : grants.stream()
                    .filter(MINIAPP_STAFF_CAPABILITY_ALLOWLIST::contains)
                    .sorted()
                    .toList();
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

    private MiniappChannelRow channelByAppId(
            String appId,
            boolean forUpdate) {
        String normalized = appId == null ? "" : appId.trim();
        return jdbc.query("""
                        SELECT id AS miniapp_channel_id, appid, app_secret,
                               login_enabled, activated_at
                        FROM iam_miniapp_channel
                        WHERE appid = ?
                        %s
                        """.formatted(forUpdate ? "FOR UPDATE" : ""),
                (rs, ignored) -> miniappChannelRow(rs),
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
                        UUID.fromString(rs.getString("staff_account_uid")),
                        rs.getString("account_kind"),
                        rs.getString("display_name"),
                        rs.getBoolean("enabled"),
                        rs.getLong("auth_version")),
                tenantId, staffId).stream().findFirst().orElse(null);
    }

    private static void requireEnabled(MiniappChannelRow row) {
        if (row.appSecret() == null
                || row.appSecret().isBlank()
                || !row.loginEnabled()
                || row.activatedAt() == null) {
            throw new TargetApiException(
                    403,
                    "IDENTITY.MINIAPP_LOGIN_DISABLED",
                    "当前小程序登录入口不可用");
        }
    }

    private static void requireSubjectActive(WechatSubjectRow subject) {
        if (!"ACTIVE".equals(subject.status())) {
            throw new TargetApiException(
                    403,
                    "IDENTITY.WECHAT_SUBJECT_FROZEN",
                    "当前微信身份已冻结");
        }
    }

    private static void requireAvailable(OrganizationScope scope) {
        if (!"ENABLED".equals(scope.tenantStatus())
                || !"ENABLED".equals(scope.organizationStatus())
                || !"ACTIVE".equals(scope.bindingStatus())) {
            throw new TargetApiException(
                    422,
                    "IDENTITY.DEVICE_ENTRY_INVALID",
                    "设备入口无效或当前不可用于注册");
        }
    }

    private static MiniappSessionCreated created(
            String token,
            String audience,
            String entryMode,
            Instant expiresAt,
            OrganizationScope scope,
            UUID subjectUid,
            UUID organizationUserUid,
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
                        scope.organizationCode(),
                        scope.organizationName()),
                subjectUid,
                organizationUserUid,
                displayName,
                capabilities,
                phoneBound,
                newRegistration);
    }

    private static LoginSelection loginSelectionRow(ResultSet rs)
            throws SQLException {
        return new LoginSelection(
                new WechatSubjectRow(
                        rs.getLong("wechat_subject_id"),
                        UUID.fromString(rs.getString("wechat_subject_uid")),
                        rs.getString("subject_status"),
                        rs.getLong("subject_auth_version")),
                organizationScopeRow(rs),
                organizationUserRow(rs));
    }

    private static OrganizationScope organizationScopeRow(ResultSet rs)
            throws SQLException {
        return new OrganizationScope(
                rs.getLong("tenant_id"),
                rs.getString("tenant_code"),
                rs.getString("tenant_status"),
                rs.getLong("organization_id"),
                rs.getString("organization_code"),
                rs.getString("organization_name"),
                rs.getString("organization_status"),
                rs.getString("binding_status"));
    }

    private static MiniappChannelRow miniappChannelRow(ResultSet rs)
            throws SQLException {
        return new MiniappChannelRow(
                rs.getLong("miniapp_channel_id"),
                rs.getString("appid"),
                rs.getString("app_secret"),
                rs.getBoolean("login_enabled"),
                nullableInstant(rs, "activated_at"));
    }

    private static OrganizationUserRow organizationUserRow(ResultSet rs)
            throws SQLException {
        long assetId = rs.getLong("registered_via_asset_id");
        boolean assetIdWasNull = rs.wasNull();
        return new OrganizationUserRow(
                rs.getLong("id"),
                UUID.fromString(rs.getString("organization_user_uid")),
                rs.getLong("tenant_id"),
                rs.getLong("organization_id"),
                rs.getLong("miniapp_channel_id"),
                rs.getLong("wechat_subject_id"),
                rs.getString("phone_e164"),
                nullableInstant(rs, "phone_bound_at"),
                rs.getString("nickname"),
                rs.getString("status"),
                rs.getLong("auth_version"),
                instant(rs, "registered_at"),
                assetIdWasNull ? null : assetId);
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

    record MiniappChannelRow(
            long id,
            String appId,
            String appSecret,
            boolean loginEnabled,
            Instant activatedAt) {
    }

    record WechatSubjectRow(
            long id,
            UUID uid,
            String status,
            long authVersion) {
    }

    record OrganizationScope(
            long tenantId,
            String tenantCode,
            String tenantStatus,
            long organizationId,
            String organizationCode,
            String organizationName,
            String organizationStatus,
            String bindingStatus) {
    }

    record OrganizationUserRow(
            long id,
            UUID uid,
            long tenantId,
            long organizationId,
            long channelId,
            long subjectId,
            String phoneE164,
            Instant phoneBoundAt,
            String nickname,
            String status,
            long authVersion,
            Instant registeredAt,
            Long registeredViaAssetId) {
    }

    record LoginSelection(
            WechatSubjectRow subject,
            OrganizationScope scope,
            OrganizationUserRow user) {
    }

    private record ResolvedAsset(
            long tenantId,
            String tenantCode,
            long organizationId,
            String organizationCode,
            String organizationName,
            long assetId) {
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
