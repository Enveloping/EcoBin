package org.enveloping.ecobin.identity.application.directory;

import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.audit.SuccessfulAudit;
import org.enveloping.ecobin.framework.web.TargetWebAuditRequestContext;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.application.web.TargetWebActor;
import org.enveloping.ecobin.identity.application.web.TargetWebActorContext;
import org.enveloping.ecobin.identity.infrastructure.persistence.v1.TargetIdentitySessionRepository;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.AccountVersionCommand;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.ChangeOwnPasswordRequest;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.PageData;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.ResetPasswordRequest;
import org.enveloping.ecobin.identity.web.v1.directory.PlatformAdminModels.CreatePlatformAdminRequest;
import org.enveloping.ecobin.identity.web.v1.directory.PlatformAdminModels.DeletePlatformAdminRequest;
import org.enveloping.ecobin.identity.web.v1.directory.PlatformAdminModels.PlatformAdminView;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.crypto.password.PasswordEncoder;
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
public class TargetPlatformAdminAccountService {

    private static final int DEFAULT_PAGE_SIZE = 20;
    private static final int MAX_PAGE_SIZE = 200;
    private static final Pattern LOGIN_NAME =
            Pattern.compile("^[a-z0-9][a-z0-9._-]{0,63}$");
    private static final Set<String> STATUSES =
            Set.of("ACTIVE", "DISABLED", "DELETED");
    private static final String STATUS_SQL = """
            CASE
                WHEN deleted_at IS NOT NULL THEN 'DELETED'
                WHEN enabled = 1 THEN 'ACTIVE'
                ELSE 'DISABLED'
            END
            """;

    private final JdbcTemplate jdbc;
    private final PasswordEncoder passwordEncoder;
    private final TargetIdentitySessionRepository sessionRepository;
    private final AuditPort auditPort;
    private final ObjectMapper objectMapper;

    public TargetPlatformAdminAccountService(
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
    public PageData<PlatformAdminView> listAdministrators(
            int requestedPage,
            int requestedPageSize,
            String requestedStatus,
            String requestedQuery) {
        TargetWebActor actor = requirePlatformActor();
        requireDefaultForRead(actor);
        int page = page(requestedPage);
        int pageSize = pageSize(requestedPageSize);
        String status = normalizeStatus(requestedStatus);
        String query = normalizeQuery(requestedQuery);

        StringBuilder where = new StringBuilder(" WHERE 1 = 1");
        List<Object> parameters = new ArrayList<>();
        if (status != null) {
            where.append(" AND ").append(STATUS_SQL).append(" = ?");
            parameters.add(status);
        }
        if (query != null) {
            String pattern = "%" + escapeLike(query) + "%";
            where.append("""
                     AND (
                         login_name LIKE ? ESCAPE '!'
                         OR LOWER(display_name) LIKE ? ESCAPE '!'
                     )
                    """);
            parameters.add(pattern);
            parameters.add(pattern);
        }

        Long total = jdbc.queryForObject(
                "SELECT COUNT(*) FROM iam_platform_admin" + where,
                Long.class,
                parameters.toArray());
        List<Object> pageParameters = new ArrayList<>(parameters);
        pageParameters.add(pageSize);
        pageParameters.add((page - 1L) * pageSize);
        List<PlatformAdminView> items = jdbc.query("""
                        SELECT id, platform_admin_uid, admin_kind, login_name,
                               password_hash, display_name, enabled,
                               auth_version, lock_version, created_at,
                               updated_at, deleted_at
                        FROM iam_platform_admin
                        """ + where + " " + """
                        ORDER BY CASE WHEN admin_kind = 'DEFAULT' THEN 0 ELSE 1 END,
                                 created_at,
                                 id
                        LIMIT ? OFFSET ?
                        """,
                (rs, ignored) -> view(row(rs)),
                pageParameters.toArray());
        return new PageData<>(
                List.copyOf(items),
                page,
                pageSize,
                total == null ? 0 : total);
    }

    @Transactional(readOnly = true)
    public PlatformAdminView getAdministrator(UUID platformAdminUid) {
        TargetWebActor actor = requirePlatformActor();
        requireDefaultForRead(actor);
        return view(administratorByUid(platformAdminUid, false));
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public PlatformAdminView createAdministrator(
            UUID operationUid,
            CreatePlatformAdminRequest request) {
        String loginName = normalizeLogin(request.loginName());
        return command(
                operationUid,
                "identity.platform-admin.create",
                "platform-admin-login:" + loginName,
                request,
                true,
                () -> view(administratorByLogin(loginName, false)),
                () -> {
                    try {
                        jdbc.update("""
                                        INSERT INTO iam_platform_admin (
                                            platform_admin_uid, admin_kind,
                                            login_name, password_hash,
                                            display_name, enabled,
                                            failed_login_count, locked_until,
                                            auth_version, password_changed_at,
                                            deleted_at, lock_version,
                                            created_at, updated_at
                                        ) VALUES (
                                            ?, 'STANDARD', ?, ?, ?, 1,
                                            0, NULL, 0, CURRENT_TIMESTAMP(3),
                                            NULL, 0,
                                            CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
                                        )
                                        """,
                                UUID.randomUUID().toString(),
                                loginName,
                                passwordEncoder.encode(
                                        request.initialPassword()),
                                request.displayName().trim());
                    } catch (DataIntegrityViolationException conflict) {
                        throw conflict(
                                "IDENTITY.PLATFORM_ADMIN_LOGIN_ALREADY_USED",
                                "平台管理员登录名已被使用");
                    }
                    PlatformAdminView created = view(
                            administratorByLogin(loginName, false));
                    return result(created, null, created, null);
                });
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public PlatformAdminView changeStatus(
            UUID operationUid,
            UUID platformAdminUid,
            AccountVersionCommand request,
            boolean enabled) {
        return command(
                operationUid,
                enabled
                        ? "identity.platform-admin.activate"
                        : "identity.platform-admin.deactivate",
                "platform-admin:" + platformAdminUid,
                request,
                true,
                () -> view(administratorByUid(platformAdminUid, false)),
                () -> {
                    PlatformAdminRow target = administratorByUid(
                            platformAdminUid, true);
                    protectDefault(target);
                    requireNotDeleted(target);
                    requireVersions(target, request.expectedVersion(),
                            request.expectedAuthVersion());
                    if (target.enabled() == enabled) {
                        throw conflict(
                                "IDENTITY.PLATFORM_ADMIN_STATE_CONFLICT",
                                "平台管理员已经处于目标状态");
                    }
                    PlatformAdminView before = view(target);
                    jdbc.update("""
                                    UPDATE iam_platform_admin
                                    SET enabled = ?,
                                        failed_login_count = CASE
                                            WHEN ? = 1 THEN 0
                                            ELSE failed_login_count
                                        END,
                                        locked_until = CASE
                                            WHEN ? = 1 THEN NULL
                                            ELSE locked_until
                                        END,
                                        auth_version = auth_version + 1,
                                        lock_version = lock_version + 1,
                                        updated_at = CURRENT_TIMESTAMP(3)
                                    WHERE id = ?
                                    """,
                            enabled,
                            enabled,
                            enabled,
                            target.id());
                    sessionRepository.revokeAllPlatformSessions(
                            target.id(),
                            enabled
                                    ? "PLATFORM_ADMIN_REACTIVATED"
                                    : "PLATFORM_ADMIN_DISABLED");
                    PlatformAdminView after = view(
                            administratorById(target.id(), false));
                    return result(after, before, after, request.reason());
                });
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public PlatformAdminView resetPassword(
            UUID operationUid,
            UUID platformAdminUid,
            ResetPasswordRequest request) {
        return command(
                operationUid,
                "identity.platform-admin.password-reset",
                "platform-admin:" + platformAdminUid,
                request,
                true,
                () -> view(administratorByUid(platformAdminUid, false)),
                () -> {
                    PlatformAdminRow target = administratorByUid(
                            platformAdminUid, true);
                    protectDefault(target);
                    requireNotDeleted(target);
                    requireVersions(target, request.expectedVersion(),
                            request.expectedAuthVersion());
                    PlatformAdminView before = view(target);
                    updatePassword(target, request.newPassword());
                    PlatformAdminView after = view(
                            administratorById(target.id(), false));
                    return result(after, before, after, null);
                });
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public PlatformAdminView deleteAdministrator(
            UUID operationUid,
            UUID platformAdminUid,
            DeletePlatformAdminRequest request) {
        return command(
                operationUid,
                "identity.platform-admin.delete",
                "platform-admin:" + platformAdminUid,
                request,
                true,
                () -> view(administratorByUid(platformAdminUid, false)),
                () -> {
                    PlatformAdminRow target = administratorByUid(
                            platformAdminUid, true);
                    protectDefault(target);
                    requireNotDeleted(target);
                    requireVersions(target, request.expectedVersion(),
                            request.expectedAuthVersion());
                    PlatformAdminView before = view(target);
                    jdbc.update("""
                                    UPDATE iam_platform_admin
                                    SET enabled = 0,
                                        deleted_at = CURRENT_TIMESTAMP(3),
                                        auth_version = auth_version + 1,
                                        lock_version = lock_version + 1,
                                        updated_at = CURRENT_TIMESTAMP(3)
                                    WHERE id = ?
                                    """,
                            target.id());
                    sessionRepository.revokeAllPlatformSessions(
                            target.id(), "PLATFORM_ADMIN_DELETED");
                    PlatformAdminView after = view(
                            administratorById(target.id(), false));
                    return result(
                            after, before, after, request.reason().trim());
                });
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public PlatformAdminView changeOwnPassword(
            UUID operationUid,
            ChangeOwnPasswordRequest request) {
        TargetWebActor actor = requirePlatformActor();
        return command(
                operationUid,
                "identity.platform-admin.password-change",
                "platform-admin:" + actor.principalUid(),
                request,
                false,
                () -> view(administratorByUid(
                        actor.principalUid(), false)),
                () -> {
                    PlatformAdminRow target = administratorByUid(
                            actor.principalUid(), true);
                    requireNotDeleted(target);
                    requireVersions(target, request.expectedVersion(),
                            request.expectedAuthVersion());
                    if (!passwordEncoder.matches(
                            request.currentPassword(),
                            target.passwordHash())) {
                        throw new TargetApiException(
                                403,
                                "AUTH.CURRENT_PASSWORD_INVALID",
                                "当前密码不正确");
                    }
                    PlatformAdminView before = view(target);
                    updatePassword(target, request.newPassword());
                    PlatformAdminView after = view(
                            administratorById(target.id(), false));
                    return result(after, before, after, null);
                });
    }

    private void updatePassword(
            PlatformAdminRow target,
            String newPassword) {
        jdbc.update("""
                        UPDATE iam_platform_admin
                        SET password_hash = ?,
                            password_changed_at = CURRENT_TIMESTAMP(3),
                            failed_login_count = 0,
                            locked_until = NULL,
                            auth_version = auth_version + 1,
                            lock_version = lock_version + 1,
                            updated_at = CURRENT_TIMESTAMP(3)
                        WHERE id = ?
                        """,
                passwordEncoder.encode(newPassword),
                target.id());
        sessionRepository.revokeAllPlatformSessions(
                target.id(), "PASSWORD_CHANGED");
    }

    private <T> T command(
            UUID operationUid,
            String actionCode,
            String targetIdentity,
            Object request,
            boolean defaultRequired,
            Supplier<T> replayWork,
            Supplier<CommandResult<T>> work) {
        validateOperationUid(operationUid);
        TargetWebAuditRequestContext.describe(actionCode, targetIdentity);
        TargetWebActor actor = requirePlatformActor();
        lockAndRevalidateActor(actor, defaultRequired);
        String fingerprint = fingerprint(
                actor, actionCode, targetIdentity, request);
        Optional<SuccessfulAudit> prior =
                auditPort.findSuccessful(operationUid);
        if (prior.isPresent()) {
            return replay(
                    prior.get(), actor, actionCode, fingerprint, replayWork);
        }
        CommandResult<T> result = work.get();
        String safeSummary = writeJson(new SafeChange(
                fingerprint,
                result.before(),
                result.after(),
                Map.of("reasonPresent",
                        result.reason() != null
                                && !result.reason().isBlank())));
        auditPort.append(new AuditEntry(
                UUID.randomUUID(),
                UUID.randomUUID(),
                operationUid,
                AuditScopeKind.PLATFORM,
                null,
                null,
                AuditActorKind.PLATFORM_ADMIN,
                actor.principalId(),
                null,
                null,
                null,
                actor.displayName(),
                actionCode,
                "platform-admin",
                result.response() instanceof PlatformAdminView view
                        ? view.platformAdminUid().toString()
                        : targetIdentity,
                "WEB",
                "SUCCEEDED",
                actor.sessionUid(),
                blankToNull(result.reason()),
                safeSummary,
                Instant.now()));
        return result.response();
    }

    private void requireDefaultForRead(TargetWebActor actor) {
        PlatformAdminRow current = administratorById(
                actor.principalId(), false);
        if (!current.enabled()
                || current.deletedAt() != null
                || current.authVersion() != actor.authVersion()) {
            throw invalidSession();
        }
        if (!"DEFAULT".equals(current.adminKind())
                || !actor.effectiveCapabilities()
                .contains("platform-account.manage")) {
            throw forbidden();
        }
    }

    private void lockAndRevalidateActor(
            TargetWebActor actor,
            boolean defaultRequired) {
        PlatformAdminRow current;
        try {
            current = administratorById(actor.principalId(), true);
        } catch (TargetApiException missing) {
            throw invalidSession();
        }
        if (!current.enabled()
                || current.deletedAt() != null
                || current.authVersion() != actor.authVersion()) {
            throw invalidSession();
        }
        if (defaultRequired
                && (!"DEFAULT".equals(current.adminKind())
                || !actor.effectiveCapabilities()
                .contains("platform-account.manage"))) {
            throw forbidden();
        }
    }

    private <T> T replay(
            SuccessfulAudit audit,
            TargetWebActor actor,
            String actionCode,
            String fingerprint,
            Supplier<T> replayWork) {
        JsonNode summary = readJson(audit.safeChangeSummaryJson());
        if (audit.actorKind() != AuditActorKind.PLATFORM_ADMIN
                || !Objects.equals(
                audit.platformAdminId(), actor.principalId())
                || !audit.actionCode().equals(actionCode)
                || !fingerprint.equals(
                summary.path("fingerprint").asText())) {
            throw conflict(
                    "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                    "相同操作标识已绑定到不同请求");
        }
        return replayWork.get();
    }

    private PlatformAdminRow administratorByUid(
            UUID uid,
            boolean lock) {
        List<PlatformAdminRow> rows = jdbc.query("""
                        SELECT id, platform_admin_uid, admin_kind, login_name,
                               password_hash, display_name, enabled,
                               auth_version, lock_version, created_at,
                               updated_at, deleted_at
                        FROM iam_platform_admin
                        WHERE platform_admin_uid = ?
                        %s
                        """.formatted(lock ? "FOR UPDATE" : ""),
                (rs, ignored) -> row(rs),
                uid.toString());
        if (rows.isEmpty()) {
            throw notFound();
        }
        return rows.getFirst();
    }

    private PlatformAdminRow administratorByLogin(
            String loginName,
            boolean lock) {
        List<PlatformAdminRow> rows = jdbc.query("""
                        SELECT id, platform_admin_uid, admin_kind, login_name,
                               password_hash, display_name, enabled,
                               auth_version, lock_version, created_at,
                               updated_at, deleted_at
                        FROM iam_platform_admin
                        WHERE login_name = ?
                        %s
                        """.formatted(lock ? "FOR UPDATE" : ""),
                (rs, ignored) -> row(rs),
                loginName);
        if (rows.isEmpty()) {
            throw notFound();
        }
        return rows.getFirst();
    }

    private PlatformAdminRow administratorById(
            long id,
            boolean lock) {
        List<PlatformAdminRow> rows = jdbc.query("""
                        SELECT id, platform_admin_uid, admin_kind, login_name,
                               password_hash, display_name, enabled,
                               auth_version, lock_version, created_at,
                               updated_at, deleted_at
                        FROM iam_platform_admin
                        WHERE id = ?
                        %s
                        """.formatted(lock ? "FOR UPDATE" : ""),
                (rs, ignored) -> row(rs),
                id);
        if (rows.isEmpty()) {
            throw notFound();
        }
        return rows.getFirst();
    }

    private static PlatformAdminRow row(ResultSet rs)
            throws SQLException {
        return new PlatformAdminRow(
                rs.getLong("id"),
                UUID.fromString(rs.getString("platform_admin_uid")),
                rs.getString("admin_kind"),
                rs.getString("login_name"),
                rs.getString("password_hash"),
                rs.getString("display_name"),
                rs.getBoolean("enabled"),
                rs.getLong("auth_version"),
                rs.getLong("lock_version"),
                instant(rs, "created_at"),
                instant(rs, "updated_at"),
                nullableInstant(rs, "deleted_at"));
    }

    private static PlatformAdminView view(PlatformAdminRow row) {
        return new PlatformAdminView(
                row.uid(),
                row.adminKind(),
                row.loginName(),
                row.displayName(),
                row.deletedAt() != null
                        ? "DELETED"
                        : row.enabled() ? "ACTIVE" : "DISABLED",
                row.version(),
                row.authVersion(),
                row.createdAt(),
                row.updatedAt(),
                row.deletedAt());
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
            throw new IllegalStateException(
                    "SHA-256 is unavailable", exception);
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

    private JsonNode readJson(String value) {
        try {
            return objectMapper.readTree(value);
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "safe audit summary cannot be decoded", exception);
        }
    }

    private static String normalizeLogin(String value) {
        String normalized = value == null
                ? ""
                : value.trim().toLowerCase(Locale.ROOT);
        if (!LOGIN_NAME.matcher(normalized).matches()) {
            throw invalid("登录名格式不正确");
        }
        return normalized;
    }

    private static String normalizeStatus(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        String normalized = value.trim().toUpperCase(Locale.ROOT);
        if (!STATUSES.contains(normalized)) {
            throw invalid("平台管理员状态筛选值不正确");
        }
        return normalized;
    }

    private static String normalizeQuery(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        String normalized = value.trim().toLowerCase(Locale.ROOT);
        if (normalized.length() > 200) {
            throw invalid("查询条件过长");
        }
        return normalized;
    }

    private static String escapeLike(String value) {
        return value.replace("!", "!!")
                .replace("%", "!%")
                .replace("_", "!_");
    }

    private static int page(int value) {
        return value < 1 ? 1 : value;
    }

    private static int pageSize(int value) {
        return value < 1
                ? DEFAULT_PAGE_SIZE
                : Math.min(value, MAX_PAGE_SIZE);
    }

    private static TargetWebActor requirePlatformActor() {
        TargetWebActor actor = TargetWebActorContext.required();
        if (!actor.platform()) {
            throw forbidden();
        }
        return actor;
    }

    private static void protectDefault(PlatformAdminRow target) {
        if ("DEFAULT".equals(target.adminKind())) {
            throw conflict(
                    "IDENTITY.DEFAULT_PLATFORM_ADMIN_PROTECTED",
                    "默认平台管理员不能被停用、删除或由他人重置密码");
        }
    }

    private static void requireNotDeleted(PlatformAdminRow target) {
        if (target.deletedAt() != null) {
            throw conflict(
                    "IDENTITY.PLATFORM_ADMIN_DELETED",
                    "已删除的平台管理员不能再次修改或恢复");
        }
    }

    private static void requireVersions(
            PlatformAdminRow target,
            long expectedVersion,
            long expectedAuthVersion) {
        if (target.version() != expectedVersion) {
            throw versionConflict(target.version());
        }
        if (target.authVersion() != expectedAuthVersion) {
            throw versionConflict(target.authVersion());
        }
    }

    private static void validateOperationUid(UUID operationUid) {
        if (operationUid == null || operationUid.version() != 4) {
            throw invalid("Idempotency-Key 必须是 UUIDv4");
        }
    }

    private static String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value.trim();
    }

    private static TargetApiException invalid(String message) {
        return new TargetApiException(
                400, "COMMON.INVALID_REQUEST", message);
    }

    private static TargetApiException forbidden() {
        return new TargetApiException(
                403,
                "AUTH.CAPABILITY_REQUIRED",
                "当前账号缺少平台管理员治理能力");
    }

    private static TargetApiException invalidSession() {
        return new TargetApiException(
                401,
                "AUTH.SESSION_INVALID",
                "登录状态已经变化，请重新登录");
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404,
                "RESOURCE.NOT_FOUND",
                "平台管理员不存在");
    }

    private static TargetApiException conflict(
            String code,
            String message) {
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

    private static <T> CommandResult<T> result(
            T response,
            Object before,
            Object after,
            String reason) {
        return new CommandResult<>(response, before, after, reason);
    }

    private record SafeChange(
            String fingerprint,
            Object before,
            Object after,
            Map<String, Object> metadata) {
    }

    private record CommandResult<T>(
            T response,
            Object before,
            Object after,
            String reason) {
    }

    private record PlatformAdminRow(
            long id,
            UUID uid,
            String adminKind,
            String loginName,
            String passwordHash,
            String displayName,
            boolean enabled,
            long authVersion,
            long version,
            Instant createdAt,
            Instant updatedAt,
            Instant deletedAt) {
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
}
