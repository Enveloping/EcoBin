package org.enveloping.ecobin.funds.application.binding;

import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.funds.application.access.FundsAccessService;
import org.enveloping.ecobin.funds.application.access.FundsAccessService.WebScope;
import org.enveloping.ecobin.funds.web.v1.FundsModels.DisableMerchantBindingRequest;
import org.enveloping.ecobin.funds.web.v1.FundsModels.MerchantBindingView;
import org.enveloping.ecobin.funds.web.v1.FundsModels.VerifyMerchantBindingRequest;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.UUID;

@Service
public class MerchantBindingApplicationService {

    private final JdbcTemplate jdbc;
    private final FundsAccessService access;
    private final AuditPort audit;

    public MerchantBindingApplicationService(
            JdbcTemplate jdbc,
            FundsAccessService access,
            AuditPort audit) {
        this.jdbc = jdbc;
        this.access = access;
        this.audit = audit;
    }

    @Transactional(readOnly = true)
    public MerchantBindingView get(
            String tenantCode, String organizationCode) {
        WebScope scope = scope(tenantCode, organizationCode);
        return view(requiredMiniapp(scope, false), binding(scope, false));
    }

    @Transactional
    public MerchantBindingView verify(
            String tenantCode,
            String organizationCode,
            UUID operationUid,
            VerifyMerchantBindingRequest request) {
        if (request == null || request.expectedMiniappVersion() == null) {
            throw validation("expectedMiniappVersion 必须提供");
        }
        WebScope scope = scope(tenantCode, organizationCode);
        Miniapp miniapp = requiredMiniapp(scope, true);
        if (miniapp.version() != request.expectedMiniappVersion()) {
            throw versionConflict("机构小程序配置已变化，请刷新后重新核查");
        }
        if (!miniapp.enabled()) {
            throw new TargetApiException(
                    422, "FUNDS.MINIAPP_NOT_ACTIVE",
                    "机构小程序当前未启用，不能核查微信商户绑定");
        }
        Binding current = binding(scope, true);
        if (current == null && request.expectedBindingVersion() != null) {
            throw versionConflict("商户绑定尚未建立");
        }
        if (current != null && (request.expectedBindingVersion() == null
                || current.version() != request.expectedBindingVersion())) {
            throw versionConflict("商户绑定已被其他管理员修改，请刷新后重试");
        }
        Merchant merchant = requiredEnabledMerchant();
        LocalDateTime now = databaseNow();
        if (current == null) {
            jdbc.update("""
                    INSERT INTO fund_miniapp_merchant_binding (
                        binding_uid, tenant_id, organization_id,
                        organization_miniapp_id, appid,
                        miniapp_lock_version_snapshot, merchant_profile_id,
                        status, verified_by_platform_admin_id, verified_at,
                        disabled_at, lock_version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'VERIFIED', ?, ?, NULL,
                              0, ?, ?)
                    """, UUID.randomUUID().toString(), scope.tenantId(),
                    scope.organizationId(), miniapp.id(), miniapp.appid(),
                    miniapp.version(), merchant.id(), scope.platformAdminId(),
                    now, now, now);
        } else {
            int updated = jdbc.update("""
                    UPDATE fund_miniapp_merchant_binding
                    SET organization_miniapp_id = ?, appid = ?,
                        miniapp_lock_version_snapshot = ?,
                        merchant_profile_id = ?, status = 'VERIFIED',
                        verified_by_platform_admin_id = ?, verified_at = ?,
                        disabled_at = NULL, lock_version = lock_version + 1,
                        updated_at = ?
                    WHERE id = ? AND lock_version = ?
                    """, miniapp.id(), miniapp.appid(), miniapp.version(),
                    merchant.id(), scope.platformAdminId(), now, now,
                    current.id(), current.version());
            if (updated != 1) throw versionConflict("商户绑定版本冲突");
        }
        appendAudit(scope, operationUid, "wechat-merchant-binding.verify",
                request.note(), "已核查机构 AppID 与系统微信商户号的外部绑定关系");
        return view(miniapp, binding(scope, false));
    }

    @Transactional
    public MerchantBindingView disable(
            String tenantCode,
            String organizationCode,
            UUID operationUid,
            DisableMerchantBindingRequest request) {
        if (request == null || request.expectedBindingVersion() == null) {
            throw validation("expectedBindingVersion 必须提供");
        }
        WebScope scope = scope(tenantCode, organizationCode);
        Miniapp miniapp = requiredMiniapp(scope, true);
        Binding current = binding(scope, true);
        if (current == null) throw notFound("机构尚未建立微信商户绑定");
        if (current.version() != request.expectedBindingVersion()) {
            throw versionConflict("商户绑定已被其他管理员修改，请刷新后重试");
        }
        if (!"VERIFIED".equals(current.status())) {
            throw new TargetApiException(
                    409, "FUNDS.MERCHANT_BINDING_STATE_CONFLICT",
                    "当前商户绑定已经不是可禁用状态");
        }
        LocalDateTime now = databaseNow();
        int updated = jdbc.update("""
                UPDATE fund_miniapp_merchant_binding
                SET status = 'DISABLED', disabled_at = ?,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE id = ? AND lock_version = ? AND status = 'VERIFIED'
                """, now, now, current.id(), current.version());
        if (updated != 1) throw versionConflict("商户绑定版本冲突");
        appendAudit(scope, operationUid, "wechat-merchant-binding.disable",
                request.reason(), "已禁用机构 AppID 与系统微信商户号的本地就绪事实");
        return view(miniapp, binding(scope, false));
    }

    private WebScope scope(String tenantCode, String organizationCode) {
        return access.webScope(
                true, tenantCode, organizationCode,
                "funds.merchant-binding.manage", false);
    }

    private Miniapp requiredMiniapp(WebScope scope, boolean lock) {
        List<Miniapp> rows = jdbc.query("""
                SELECT id, appid, login_enabled, lock_version
                FROM iam_organization_miniapp
                WHERE tenant_id = ? AND organization_id = ?
                """ + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> new Miniapp(
                        rs.getLong("id"), rs.getString("appid"),
                        rs.getBoolean("login_enabled"),
                        rs.getLong("lock_version")),
                scope.tenantId(), scope.organizationId());
        if (rows.isEmpty()) throw notFound("机构尚未配置小程序");
        return rows.getFirst();
    }

    private Binding binding(WebScope scope, boolean lock) {
        List<Binding> rows = jdbc.query("""
                SELECT b.id, b.status, b.appid, b.miniapp_lock_version_snapshot,
                       b.lock_version, b.verified_at, b.disabled_at, m.mchid
                FROM fund_miniapp_merchant_binding b
                JOIN fund_wechat_merchant_profile m
                  ON m.id = b.merchant_profile_id
                WHERE b.tenant_id = ? AND b.organization_id = ?
                """ + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> new Binding(
                        rs.getLong("id"), rs.getString("status"),
                        rs.getString("appid"),
                        rs.getLong("miniapp_lock_version_snapshot"),
                        rs.getLong("lock_version"), rs.getString("mchid"),
                        rs.getObject("verified_at", LocalDateTime.class),
                        rs.getObject("disabled_at", LocalDateTime.class)),
                scope.tenantId(), scope.organizationId());
        return rows.isEmpty() ? null : rows.getFirst();
    }

    private Merchant requiredEnabledMerchant() {
        List<Merchant> rows = jdbc.query("""
                SELECT id, mchid FROM fund_wechat_merchant_profile
                WHERE status = 'ENABLED' ORDER BY id
                FOR UPDATE
                """, (rs, ignored) -> new Merchant(
                        rs.getLong("id"), rs.getString("mchid")));
        if (rows.isEmpty()) {
            throw new TargetApiException(
                    422, "FUNDS.WECHAT_MERCHANT_NOT_CONFIGURED",
                    "平台尚未配置可用的微信商户资料");
        }
        if (rows.size() > 1) {
            throw new TargetApiException(
                    409, "FUNDS.WECHAT_MERCHANT_AMBIGUOUS",
                    "平台存在多个启用的微信商户资料，无法确定当前系统商户号");
        }
        return rows.getFirst();
    }

    private MerchantBindingView view(Miniapp miniapp, Binding binding) {
        if (binding == null) {
            return new MerchantBindingView(
                    "UNVERIFIED", miniapp.appid(), miniapp.version(),
                    null, null, null, null);
        }
        String status = binding.status();
        if ("VERIFIED".equals(status)
                && !binding.appid().equals(miniapp.appid())) {
            status = "STALE";
        }
        return new MerchantBindingView(
                status, miniapp.appid(), miniapp.version(), binding.version(),
                maskMerchantId(binding.mchid()), instant(binding.verifiedAt()),
                instant(binding.disabledAt()));
    }

    private void appendAudit(
            WebScope scope,
            UUID operationUid,
            String action,
            String reason,
            String summary) {
        try {
            audit.append(new AuditEntry(
                    UUID.randomUUID(), UUID.randomUUID(), operationUid,
                    AuditScopeKind.ORGANIZATION,
                    scope.tenantId(), scope.organizationId(),
                    AuditActorKind.PLATFORM_ADMIN,
                    scope.platformAdminId(), null, null, null,
                    scope.actorDisplayName(), action,
                    "WECHAT_MERCHANT_BINDING", scope.organizationCode(),
                    "WEB", "SUCCEEDED", scope.sessionUid(), trim(reason, 255),
                    summary, instant(databaseNow())));
        } catch (DuplicateKeyException duplicate) {
            throw new TargetApiException(
                    409, "REQUEST.IDEMPOTENCY_CONFLICT",
                    "该操作标识已经用于其他成功请求");
        }
    }

    private LocalDateTime databaseNow() {
        return jdbc.queryForObject("SELECT CURRENT_TIMESTAMP(3)",
                LocalDateTime.class);
    }

    private static String maskMerchantId(String mchid) {
        if (mchid == null || mchid.length() <= 4) return "****";
        return "****" + mchid.substring(mchid.length() - 4);
    }

    private static String trim(String value, int maximum) {
        if (value == null || value.isBlank()) return null;
        String trimmed = value.trim();
        return trimmed.length() <= maximum
                ? trimmed : trimmed.substring(0, maximum);
    }

    private static Instant instant(LocalDateTime value) {
        return value == null ? null : value.toInstant(ZoneOffset.UTC);
    }

    private static TargetApiException validation(String message) {
        return new TargetApiException(400, "VALIDATION.INVALID_ARGUMENT", message);
    }

    private static TargetApiException versionConflict(String message) {
        return new TargetApiException(
                409, "FUNDS.MERCHANT_BINDING_VERSION_CONFLICT", message);
    }

    private static TargetApiException notFound(String message) {
        return new TargetApiException(404, "RESOURCE.NOT_FOUND", message);
    }

    private record Miniapp(long id, String appid, boolean enabled, long version) {
    }

    private record Binding(
            long id, String status, String appid, long miniappVersion,
            long version, String mchid, LocalDateTime verifiedAt,
            LocalDateTime disabledAt) {
    }

    private record Merchant(long id, String mchid) {
    }
}
