package org.enveloping.ecobin.funds.application.binding;

import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.audit.SuccessfulAudit;
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
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.security.MessageDigest;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;

@Service
public class MerchantBindingApplicationService {

    private static final String VERIFY_ACTION =
            "wechat-merchant-binding.verify";
    private static final String DISABLE_ACTION =
            "wechat-merchant-binding.disable";
    private static final String TARGET_TYPE =
            "WECHAT_MERCHANT_BINDING";

    private final JdbcTemplate jdbc;
    private final FundsAccessService access;
    private final AuditPort audit;
    private final ObjectMapper objectMapper;

    public MerchantBindingApplicationService(
            JdbcTemplate jdbc,
            FundsAccessService access,
            AuditPort audit,
            ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.access = access;
        this.audit = audit;
        this.objectMapper = objectMapper;
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
        requireUuidV4(operationUid);
        WebScope scope = scope(tenantCode, organizationCode);
        Miniapp miniapp = requiredMiniapp(scope, true);
        String fingerprint = verifyFingerprint(scope, request);
        Optional<SuccessfulAudit> previous =
                audit.findSuccessful(operationUid);
        if (previous.isPresent()) {
            return replay(previous.orElseThrow(), scope,
                    VERIFY_ACTION, fingerprint);
        }
        if (request == null || request.expectedMiniappVersion() == null) {
            throw validation("expectedMiniappVersion 必须提供");
        }
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
            requireSameBindingIdentity(current, miniapp, merchant);
            int updated = jdbc.update("""
                    UPDATE fund_miniapp_merchant_binding
                    SET status = 'VERIFIED', disabled_at = NULL,
                        lock_version = lock_version + 1,
                        updated_at = ?
                    WHERE id = ? AND lock_version = ?
                    """, now, current.id(), current.version());
            if (updated != 1) throw versionConflict("商户绑定版本冲突");
        }
        MerchantBindingView response = view(
                miniapp, binding(scope, false));
        appendAudit(scope, operationUid, VERIFY_ACTION,
                request.note(), fingerprint, response, "VERIFIED",
                "已核查机构 AppID 与系统微信商户号的外部绑定关系");
        return response;
    }

    @Transactional
    public MerchantBindingView disable(
            String tenantCode,
            String organizationCode,
            UUID operationUid,
            DisableMerchantBindingRequest request) {
        requireUuidV4(operationUid);
        WebScope scope = scope(tenantCode, organizationCode);
        Miniapp miniapp = requiredMiniapp(scope, true);
        String fingerprint = disableFingerprint(scope, request);
        Optional<SuccessfulAudit> previous =
                audit.findSuccessful(operationUid);
        if (previous.isPresent()) {
            return replay(previous.orElseThrow(), scope,
                    DISABLE_ACTION, fingerprint);
        }
        if (request == null || request.expectedBindingVersion() == null) {
            throw validation("expectedBindingVersion 必须提供");
        }
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
        MerchantBindingView response = view(
                miniapp, binding(scope, false));
        appendAudit(scope, operationUid, DISABLE_ACTION,
                request.reason(), fingerprint, response, "DISABLED",
                "已禁用机构 AppID 与系统微信商户号的本地就绪事实");
        return response;
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
                SELECT b.id, b.organization_miniapp_id,
                       b.merchant_profile_id, b.status, b.appid,
                       b.miniapp_lock_version_snapshot, b.lock_version,
                       b.verified_at, b.disabled_at, m.mchid
                FROM fund_miniapp_merchant_binding b
                JOIN fund_wechat_merchant_profile m
                  ON m.id = b.merchant_profile_id
                WHERE b.tenant_id = ? AND b.organization_id = ?
                """ + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> new Binding(
                        rs.getLong("id"),
                        rs.getLong("organization_miniapp_id"),
                        rs.getLong("merchant_profile_id"),
                        rs.getString("status"),
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
            String fingerprint,
            MerchantBindingView response,
            String bindingStatus,
            String description) {
        try {
            audit.append(new AuditEntry(
                    UUID.randomUUID(), UUID.randomUUID(), operationUid,
                    AuditScopeKind.ORGANIZATION,
                    scope.tenantId(), scope.organizationId(),
                    AuditActorKind.PLATFORM_ADMIN,
                    scope.platformAdminId(), null, null, null,
                    scope.actorDisplayName(), action,
                    TARGET_TYPE, scope.organizationCode(),
                    "WEB", "SUCCEEDED", scope.sessionUid(), trim(reason, 255),
                    writeJson(new BindingAuditSummary(
                            fingerprint, response,
                            bindingStatus, description)),
                    instant(databaseNow())));
        } catch (DuplicateKeyException duplicate) {
            throw idempotencyConflict();
        }
    }

    private MerchantBindingView replay(
            SuccessfulAudit previous,
            WebScope scope,
            String action,
            String fingerprint) {
        JsonNode summary = readJson(previous.safeChangeSummaryJson());
        if (!operationMatches(previous, scope, action)
                || !fingerprint.equals(
                summary.path("fingerprint").asText())) {
            throw idempotencyConflict();
        }
        try {
            MerchantBindingView response = objectMapper.treeToValue(
                    summary.path("response"), MerchantBindingView.class);
            if (response == null || response.status() == null) {
                throw idempotencyConflict();
            }
            return response;
        } catch (TargetApiException exception) {
            throw exception;
        } catch (Exception exception) {
            throw idempotencyConflict();
        }
    }

    private static boolean operationMatches(
            SuccessfulAudit previous,
            WebScope scope,
            String action) {
        return previous.actorKind() == AuditActorKind.PLATFORM_ADMIN
                && Objects.equals(previous.platformAdminId(),
                scope.platformAdminId())
                && previous.scopeKind() == AuditScopeKind.ORGANIZATION
                && Objects.equals(previous.tenantId(), scope.tenantId())
                && Objects.equals(
                previous.organizationId(), scope.organizationId())
                && action.equals(previous.actionCode())
                && TARGET_TYPE.equals(previous.targetType())
                && scope.organizationCode().equals(
                previous.targetStableKey());
    }

    private String verifyFingerprint(
            WebScope scope,
            VerifyMerchantBindingRequest request) {
        Map<String, Object> fields = new LinkedHashMap<>();
        fields.put("expectedMiniappVersion",
                request == null ? null : request.expectedMiniappVersion());
        fields.put("expectedBindingVersion",
                request == null ? null : request.expectedBindingVersion());
        fields.put("note", request == null ? null : request.note());
        return fingerprint(scope, VERIFY_ACTION, fields);
    }

    private String disableFingerprint(
            WebScope scope,
            DisableMerchantBindingRequest request) {
        Map<String, Object> fields = new LinkedHashMap<>();
        fields.put("expectedBindingVersion",
                request == null ? null : request.expectedBindingVersion());
        fields.put("reason", request == null ? null : request.reason());
        return fingerprint(scope, DISABLE_ACTION, fields);
    }

    private String fingerprint(
            WebScope scope,
            String action,
            Map<String, Object> fields) {
        Map<String, Object> canonical = new LinkedHashMap<>();
        canonical.put("principalUid", scope.actorUid());
        canonical.put("tenantId", scope.tenantId());
        canonical.put("organizationId", scope.organizationId());
        canonical.put("action", action);
        canonical.put("request", fields);
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256")
                            .digest(objectMapper.writeValueAsBytes(canonical)));
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "merchant binding fingerprint cannot be encoded",
                    exception);
        }
    }

    private JsonNode readJson(String value) {
        try {
            return objectMapper.readTree(value);
        } catch (Exception exception) {
            throw idempotencyConflict();
        }
    }

    private static void requireSameBindingIdentity(
            Binding current,
            Miniapp miniapp,
            Merchant merchant) {
        if (current.organizationMiniappId() != miniapp.id()
                || current.merchantProfileId() != merchant.id()
                || !Objects.equals(current.appid(), miniapp.appid())) {
            throw new TargetApiException(
                    409,
                    "FUNDS.MERCHANT_BINDING_IDENTITY_CONFLICT",
                    "当前小程序或商户身份与原核查事实不一致，不能覆盖原绑定证据");
        }
    }

    private String writeJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "merchant binding audit cannot be encoded", exception);
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
        return new TargetApiException(
                400, "COMMON.VALIDATION_FAILED", message);
    }

    private static void requireUuidV4(UUID operationUid) {
        if (operationUid == null
                || operationUid.version() != 4
                || operationUid.variant() != 2) {
            throw validation("Idempotency-Key 必须是 UUIDv4");
        }
    }

    private static TargetApiException idempotencyConflict() {
        return new TargetApiException(
                409,
                "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                "该 Idempotency-Key 已用于不同的机构微信商户绑定请求");
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
            long id,
            long organizationMiniappId,
            long merchantProfileId,
            String status,
            String appid,
            long miniappVersion,
            long version,
            String mchid,
            LocalDateTime verifiedAt,
            LocalDateTime disabledAt) {
    }

    private record Merchant(long id, String mchid) {
    }

    private record BindingAuditSummary(
            String fingerprint,
            MerchantBindingView response,
            String bindingStatus,
            String description) {
    }
}
