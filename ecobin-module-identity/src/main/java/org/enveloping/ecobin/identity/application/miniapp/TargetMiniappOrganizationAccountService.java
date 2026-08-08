package org.enveloping.ecobin.identity.application.miniapp;

import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.framework.security.JwtTokenProvider;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.web.v1.miniapp.MiniappModels.MiniappSessionCreated;
import org.enveloping.ecobin.identity.web.v1.miniapp.MiniappModels.OrganizationAccountList;
import org.enveloping.ecobin.identity.web.v1.miniapp.MiniappModels.OrganizationAccountSummary;
import org.enveloping.ecobin.identity.web.v1.miniapp.MiniappModels.OrganizationSummary;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.UUID;

/**
 * 微信主体可见的安全机构账号目录与单账号会话切换。
 *
 * <p>这里只跨租户读取机构名称、注册时间和绑定状态等身份摘要；钱包、订单等业务数据
 * 仍只能通过切换后签发的单租户会话读取。</p>
 */
@Service
public class TargetMiniappOrganizationAccountService {

    private final JdbcTemplate jdbc;
    private final TargetMiniappLoginTransactionService loginTransactions;
    private final JwtTokenProvider tokenProvider;

    public TargetMiniappOrganizationAccountService(
            JdbcTemplate jdbc,
            TargetMiniappLoginTransactionService loginTransactions,
            JwtTokenProvider tokenProvider) {
        this.jdbc = jdbc;
        this.loginTransactions = loginTransactions;
        this.tokenProvider = tokenProvider;
    }

    @Transactional(readOnly = true)
    public OrganizationAccountList list(TargetMiniappActor actor) {
        List<OrganizationAccountSummary> accounts = jdbc.query("""
                        SELECT u.organization_user_uid,
                               u.registered_at, u.phone_bound_at,
                               o.organization_code, o.organization_name
                        FROM iam_organization_user u
                        JOIN iam_tenant t
                          ON t.id = u.tenant_id
                         AND t.status = 'ENABLED'
                        JOIN iam_organization o
                          ON o.tenant_id = u.tenant_id
                         AND o.id = u.organization_id
                         AND o.status = 'ENABLED'
                        JOIN iam_organization_miniapp_binding b
                          ON b.tenant_id = u.tenant_id
                         AND b.organization_id = u.organization_id
                         AND b.miniapp_channel_id = u.miniapp_channel_id
                         AND b.status = 'ACTIVE'
                        WHERE u.miniapp_channel_id = ?
                          AND u.wechat_subject_id = ?
                          AND u.status = 'ACTIVE'
                        ORDER BY u.registered_at DESC, u.id DESC
                        """,
                (rs, ignored) -> new OrganizationAccountSummary(
                        UUID.fromString(
                                rs.getString("organization_user_uid")),
                        new OrganizationSummary(
                                rs.getString("organization_code"),
                                rs.getString("organization_name")),
                        instant(rs, "registered_at"),
                        UUID.fromString(rs.getString(
                                "organization_user_uid")).equals(
                                actor.organizationUserUid()),
                        nullableInstant(rs, "phone_bound_at") != null),
                actor.miniappChannelId(),
                actor.wechatSubjectId());
        return new OrganizationAccountList(accounts);
    }

    @Transactional(
            isolation = Isolation.READ_COMMITTED,
            rollbackFor = Exception.class)
    public MiniappSessionCreated select(
            UUID operationUid,
            UUID organizationUserUid,
            TargetMiniappActor actor) {
        validateUuidV4(operationUid, "Idempotency-Key");
        if (organizationUserUid == null) {
            throw new TargetApiException(
                    400,
                    "COMMON.VALIDATION_FAILED",
                    "organizationUserUid 不能为空");
        }

        MiniappSessionCreated replay = replay(
                operationUid, organizationUserUid, actor);
        if (replay != null) {
            return replay;
        }

        var channel = loginTransactions.channelById(
                actor.miniappChannelId(), true);
        var selection = loginTransactions.accountSelection(
                actor.miniappChannelId(),
                actor.wechatSubjectId(),
                organizationUserUid);
        if (selection == null) {
            throw new TargetApiException(
                    404,
                    "IDENTITY.ORGANIZATION_ACCOUNT_NOT_FOUND",
                    "机构账号不存在");
        }
        if (!"ACTIVE".equals(selection.subject().status())
                || !"ACTIVE".equals(selection.user().status())
                || !"ENABLED".equals(selection.scope().tenantStatus())
                || !"ENABLED".equals(selection.scope().organizationStatus())
                || !"ACTIVE".equals(selection.scope().bindingStatus())) {
            throw new TargetApiException(
                    403,
                    "IDENTITY.ORGANIZATION_ACCOUNT_UNAVAILABLE",
                    "机构账号当前不可用");
        }
        return loginTransactions.issueSession(
                channel, selection, false, operationUid);
    }

    private MiniappSessionCreated replay(
            UUID operationUid,
            UUID requestedUserUid,
            TargetMiniappActor actor) {
        SelectionReplay row = jdbc.query("""
                        SELECT s.session_uid, s.issued_at, s.expires_at,
                               u.organization_user_uid, u.nickname,
                               u.phone_bound_at,
                               o.organization_code, o.organization_name
                        FROM iam_organization_user_session s
                        JOIN iam_organization_user u
                          ON u.tenant_id = s.tenant_id
                         AND u.organization_id = s.organization_id
                         AND u.miniapp_channel_id = s.miniapp_channel_id
                         AND u.wechat_subject_id = s.wechat_subject_id
                         AND u.id = s.organization_user_id
                        JOIN iam_organization o
                          ON o.tenant_id = s.tenant_id
                         AND o.id = s.organization_id
                        WHERE s.selection_operation_uid = ?
                          AND s.miniapp_channel_id = ?
                          AND s.wechat_subject_id = ?
                        """,
                (rs, ignored) -> selectionReplay(rs),
                operationUid.toString(),
                actor.miniappChannelId(),
                actor.wechatSubjectId()).stream().findFirst().orElse(null);
        if (row == null) {
            return null;
        }
        if (!row.organizationUserUid().equals(requestedUserUid)) {
            throw new TargetApiException(
                    409,
                    "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                    "相同操作标识已用于其他机构账号");
        }
        String token = tokenProvider.generateTargetMiniappToken(
                row.organizationUserUid(),
                row.sessionUid(),
                TrustedAudience.MINIAPP,
                row.issuedAt(),
                row.expiresAt());
        return new MiniappSessionCreated(
                token,
                "Bearer",
                "miniapp",
                "USER",
                row.expiresAt(),
                new OrganizationSummary(
                        row.organizationCode(), row.organizationName()),
                actor.wechatSubjectUid(),
                row.organizationUserUid(),
                displayName(row.nickname()),
                List.of(),
                row.phoneBoundAt() != null,
                false);
    }

    private static SelectionReplay selectionReplay(ResultSet rs)
            throws SQLException {
        return new SelectionReplay(
                UUID.fromString(rs.getString("session_uid")),
                instant(rs, "issued_at"),
                instant(rs, "expires_at"),
                UUID.fromString(rs.getString("organization_user_uid")),
                rs.getString("nickname"),
                nullableInstant(rs, "phone_bound_at"),
                rs.getString("organization_code"),
                rs.getString("organization_name"));
    }

    private static void validateUuidV4(UUID value, String name) {
        if (value == null || value.version() != 4 || value.variant() != 2) {
            throw new TargetApiException(
                    400,
                    "COMMON.INVALID_IDEMPOTENCY_KEY",
                    name + " 必须是 UUIDv4");
        }
    }

    private static String displayName(String nickname) {
        return nickname == null || nickname.isBlank()
                ? "微信用户"
                : nickname;
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

    private record SelectionReplay(
            UUID sessionUid,
            Instant issuedAt,
            Instant expiresAt,
            UUID organizationUserUid,
            String nickname,
            Instant phoneBoundAt,
            String organizationCode,
            String organizationName) {
    }
}
