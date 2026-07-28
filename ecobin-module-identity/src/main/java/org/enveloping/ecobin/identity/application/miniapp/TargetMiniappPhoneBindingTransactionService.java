package org.enveloping.ecobin.identity.application.miniapp;

import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.audit.SuccessfulAudit;
import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.identity.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.web.v1.miniapp.MiniappModels.PhoneBindingResult;
import org.enveloping.ecobin.identity.web.v1.miniapp.MiniappModels.PhoneBindingView;
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
import java.util.HexFormat;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

@Service
public class TargetMiniappPhoneBindingTransactionService {

    private static final String ACTION = "identity.organization-user.phone.bind";

    private final JdbcTemplate jdbc;
    private final AuditPort auditPort;
    private final ObjectMapper objectMapper;

    public TargetMiniappPhoneBindingTransactionService(
            JdbcTemplate jdbc,
            AuditPort auditPort,
            ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.auditPort = auditPort;
        this.objectMapper = objectMapper;
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public PhoneBindingResult bind(
            UUID operationUid,
            TargetMiniappActor actor,
            String phoneE164) {
        validateOperationUid(operationUid);
        UserRow user = lockAndRevalidate(actor);
        String fingerprint = fingerprint(actor.principalUid(), phoneE164);
        Optional<SuccessfulAudit> prior = auditPort.findSuccessful(operationUid);
        if (prior.isPresent()) {
            validateReplay(prior.get(), user.uid(), fingerprint);
            return new PhoneBindingResult(view(user), false);
        }
        if (user.phoneE164() != null
                && !user.phoneE164().equals(phoneE164)) {
            throw conflict(
                    "IDENTITY.PHONE_ALREADY_BOUND",
                    "当前账号已经绑定其他手机号");
        }
        Long occupiedBy = jdbc.query("""
                        SELECT id
                        FROM iam_organization_user
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND phone_e164 = ?
                          AND id <> ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("id"),
                user.tenantId(),
                user.organizationId(),
                phoneE164,
                user.id()).stream().findFirst().orElse(null);
        if (occupiedBy != null) {
            throw conflict(
                    "IDENTITY.PHONE_ALREADY_USED",
                    "该手机号已绑定本机构的其他用户");
        }

        boolean created = user.phoneE164() == null;
        Instant boundAt = user.phoneBoundAt();
        if (created) {
            try {
                jdbc.update("""
                                UPDATE iam_organization_user
                                SET phone_e164 = ?,
                                    phone_bound_at = UTC_TIMESTAMP(3),
                                    lock_version = lock_version + 1,
                                    updated_at = UTC_TIMESTAMP(3)
                                WHERE tenant_id = ?
                                  AND organization_id = ?
                                  AND id = ?
                                  AND phone_e164 IS NULL
                                """,
                        phoneE164,
                        user.tenantId(),
                        user.organizationId(),
                        user.id());
            } catch (DataIntegrityViolationException exception) {
                throw conflict(
                        "IDENTITY.PHONE_ALREADY_USED",
                        "该手机号已绑定本机构的其他用户");
            }
            boundAt = jdbc.queryForObject("""
                            SELECT phone_bound_at
                            FROM iam_organization_user
                            WHERE id = ?
                            """,
                    (rs, ignored) -> instant(rs, "phone_bound_at"),
                    user.id());
        }
        PhoneBindingView response = new PhoneBindingView(
                true, mask(phoneE164), boundAt);
        appendAudit(
                operationUid,
                actor,
                user,
                fingerprint,
                created,
                response);
        return new PhoneBindingResult(response, created);
    }

    private UserRow lockAndRevalidate(TargetMiniappActor actor) {
        UserRow user = jdbc.query("""
                        SELECT u.id, u.organization_user_uid,
                               u.tenant_id, u.organization_id,
                               u.organization_miniapp_id,
                               u.phone_e164, u.phone_bound_at,
                               u.status, u.auth_version,
                               t.status AS tenant_status,
                               o.status AS organization_status,
                               m.login_enabled, m.activated_at
                        FROM iam_organization_user u
                        JOIN iam_tenant t ON t.id = u.tenant_id
                        JOIN iam_organization o
                          ON o.tenant_id = u.tenant_id
                         AND o.id = u.organization_id
                        JOIN iam_organization_miniapp m
                          ON m.tenant_id = u.tenant_id
                         AND m.organization_id = u.organization_id
                         AND m.id = u.organization_miniapp_id
                        WHERE u.id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> userRow(rs),
                actor.organizationUserId()).stream()
                .findFirst()
                .orElseThrow(TargetMiniappPhoneBindingTransactionService::rejected);
        boolean valid = actor.audience() == TrustedAudience.MINIAPP
                ? user.uid().equals(actor.principalUid())
                && user.authVersion() == actor.authVersion()
                : actor.audience() == TrustedAudience.MINIAPP_STAFF;
        valid = valid
                && user.tenantId() == actor.tenantId()
                && user.organizationId() == actor.organizationId()
                && user.miniappId() == actor.organizationMiniappId()
                && "ACTIVE".equals(user.status())
                && "ENABLED".equals(user.tenantStatus())
                && "ENABLED".equals(user.organizationStatus())
                && user.loginEnabled()
                && user.activatedAt() != null;
        if (valid && actor.audience() == TrustedAudience.MINIAPP_STAFF) {
            valid = actor.staffMiniappBindingId() != null
                    && jdbc.queryForObject("""
                                    SELECT COUNT(*)
                                    FROM iam_staff_account
                                    WHERE id = ?
                                      AND tenant_id = ?
                                      AND enabled = 1
                                      AND auth_version = ?
                                    """,
                            Integer.class,
                            actor.principalId(),
                            actor.tenantId(),
                            actor.authVersion()) == 1
                    && jdbc.queryForObject("""
                                    SELECT COUNT(*)
                                    FROM iam_staff_miniapp_binding
                                    WHERE id = ?
                                      AND tenant_id = ?
                                      AND organization_id = ?
                                      AND organization_user_id = ?
                                      AND status = 'ACTIVE'
                                    """,
                            Integer.class,
                            actor.staffMiniappBindingId(),
                            actor.tenantId(),
                            actor.organizationId(),
                            user.id()) == 1;
        }
        if (!valid) {
            throw rejected();
        }
        return user;
    }

    private void validateReplay(
            SuccessfulAudit audit,
            UUID userUid,
            String fingerprint) {
        JsonNode summary;
        try {
            summary = objectMapper.readTree(audit.safeChangeSummaryJson());
        } catch (Exception exception) {
            throw conflict(
                    "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                    "相同操作标识已绑定到不同请求");
        }
        if (audit.actorKind() != AuditActorKind.UNAUTHENTICATED
                || !ACTION.equals(audit.actionCode())
                || !userUid.toString().equals(audit.targetStableKey())
                || !fingerprint.equals(
                summary.path("fingerprint").asText())) {
            throw conflict(
                    "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                    "相同操作标识已绑定到不同请求");
        }
    }

    private void appendAudit(
            UUID operationUid,
            TargetMiniappActor actor,
            UserRow user,
            String fingerprint,
            boolean changed,
            PhoneBindingView response) {
        String summary;
        try {
            summary = objectMapper.writeValueAsString(Map.of(
                    "fingerprint", fingerprint,
                    "before", Map.of("phoneBound", !changed),
                    "after", Map.of(
                            "phoneBound", true,
                            "maskedPhoneNumber",
                            response.maskedPhoneNumber())));
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "safe audit summary cannot be encoded", exception);
        }
        auditPort.append(new AuditEntry(
                UUID.randomUUID(),
                UUID.randomUUID(),
                operationUid,
                AuditScopeKind.ORGANIZATION,
                user.tenantId(),
                user.organizationId(),
                AuditActorKind.UNAUTHENTICATED,
                null,
                null,
                null,
                actor.displayName(),
                ACTION,
                "ORGANIZATION_USER",
                user.uid().toString(),
                "SECURITY_ENTRY",
                "SUCCEEDED",
                actor.sessionUid(),
                null,
                summary,
                Instant.now()));
    }

    private static PhoneBindingView view(UserRow user) {
        if (user.phoneE164() == null || user.phoneBoundAt() == null) {
            throw conflict(
                    "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                    "原操作已成功，但当前手机号状态不一致");
        }
        return new PhoneBindingView(
                true, mask(user.phoneE164()), user.phoneBoundAt());
    }

    private static String fingerprint(UUID userUid, String phoneE164) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            digest.update(userUid.toString().getBytes(StandardCharsets.UTF_8));
            digest.update((byte) 0);
            digest.update(ACTION.getBytes(StandardCharsets.UTF_8));
            digest.update((byte) 0);
            digest.update(phoneE164.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest.digest());
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
    }

    private static void validateOperationUid(UUID operationUid) {
        if (operationUid == null
                || operationUid.version() != 4
                || operationUid.variant() != 2) {
            throw new TargetApiException(
                    400,
                    "COMMON.INVALID_IDEMPOTENCY_KEY",
                    "Idempotency-Key 必须是 UUIDv4");
        }
    }

    private static String mask(String phone) {
        if (phone.length() <= 7) {
            return "***";
        }
        return phone.substring(0, phone.length() - 8)
                + "****"
                + phone.substring(phone.length() - 4);
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

    private static UserRow userRow(ResultSet rs) throws SQLException {
        return new UserRow(
                rs.getLong("id"),
                UUID.fromString(rs.getString("organization_user_uid")),
                rs.getLong("tenant_id"),
                rs.getLong("organization_id"),
                rs.getLong("organization_miniapp_id"),
                rs.getString("phone_e164"),
                nullableInstant(rs, "phone_bound_at"),
                rs.getString("status"),
                rs.getLong("auth_version"),
                rs.getString("tenant_status"),
                rs.getString("organization_status"),
                rs.getBoolean("login_enabled"),
                nullableInstant(rs, "activated_at"));
    }

    private static TargetApiException rejected() {
        return new TargetApiException(
                401,
                "AUTH.SESSION_INVALID",
                "小程序登录状态无效，请重新登录");
    }

    private static TargetApiException conflict(String code, String message) {
        return new TargetApiException(409, code, message);
    }

    private record UserRow(
            long id,
            UUID uid,
            long tenantId,
            long organizationId,
            long miniappId,
            String phoneE164,
            Instant phoneBoundAt,
            String status,
            long authVersion,
            String tenantStatus,
            String organizationStatus,
            boolean loginEnabled,
            Instant activatedAt) {
    }
}
