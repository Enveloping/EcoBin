package org.enveloping.ecobin.identity.application.maintenance;

import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.audit.SuccessfulAudit;
import org.enveloping.ecobin.framework.web.TargetWebAuditRequestContext;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.PlatformMaintenanceSshKeyQueryPort;
import org.enveloping.ecobin.identity.api.result.ActivePlatformMaintenanceSshKey;
import org.enveloping.ecobin.identity.application.maintenance.Ed25519PublicKeyCodec.ParsedPublicKey;
import org.enveloping.ecobin.identity.application.maintenance.MaintenanceSshKeyRepository.MaintenanceSshKeyRow;
import org.enveloping.ecobin.identity.application.maintenance.MaintenanceSshKeyRepository.PlatformAdminRow;
import org.enveloping.ecobin.identity.application.web.TargetWebActor;
import org.enveloping.ecobin.identity.application.web.TargetWebActorContext;
import org.enveloping.ecobin.identity.web.v1.directory.MaintenanceSshKeyModels.CreateMaintenanceSshKeyRequest;
import org.enveloping.ecobin.identity.web.v1.directory.MaintenanceSshKeyModels.MaintenanceSshKeyView;
import org.enveloping.ecobin.identity.web.v1.directory.MaintenanceSshKeyModels.RevokeMaintenanceSshKeyRequest;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;

@Service
public class PlatformMaintenanceSshKeyService
        implements PlatformMaintenanceSshKeyQueryPort {

    private static final String TARGET_TYPE = "maintenance-ssh-key";
    private static final String CREATE_ACTION =
            "identity.maintenance-ssh-key.create";
    private static final String REVOKE_ACTION =
            "identity.maintenance-ssh-key.revoke";

    private final MaintenanceSshKeyRepository repository;
    private final MaintenanceSshKeyPersistenceRefFactory referenceFactory;
    private final AuditPort auditPort;
    private final ObjectMapper objectMapper;

    PlatformMaintenanceSshKeyService(
            MaintenanceSshKeyRepository repository,
            MaintenanceSshKeyPersistenceRefFactory referenceFactory,
            AuditPort auditPort,
            ObjectMapper objectMapper) {
        this.repository = repository;
        this.referenceFactory = referenceFactory;
        this.auditPort = auditPort;
        this.objectMapper = objectMapper;
    }

    @Transactional(readOnly = true)
    public List<MaintenanceSshKeyView> listOwnKeys() {
        TargetWebActor actor = requireActivePlatformActor(false);
        return repository.findAll(actor.principalId()).stream()
                .map(PlatformMaintenanceSshKeyService::view)
                .toList();
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public MaintenanceSshKeyView createOwnKey(
            UUID operationUid,
            CreateMaintenanceSshKeyRequest request) {
        validateOperationUid(operationUid);
        if (request == null) {
            throw invalid("请求不能为空");
        }
        String label = normalizeLabel(request.label());
        ParsedPublicKey parsed = Ed25519PublicKeyCodec.parse(
                request.publicKey());
        String targetIdentity = "maintenance-ssh-key-fingerprint:"
                + parsed.fingerprintSha256();
        TargetWebAuditRequestContext.describe(
                CREATE_ACTION, targetIdentity);
        TargetWebActor actor = requireActivePlatformActor(true);
        String requestFingerprint = requestFingerprint(
                actor,
                CREATE_ACTION,
                targetIdentity,
                new CreateRequestFingerprint(
                        label,
                        parsed.canonicalPublicKey(),
                        parsed.fingerprintSha256()));

        Optional<SuccessfulAudit> prior =
                auditPort.findSuccessful(operationUid);
        if (prior.isPresent()) {
            return replay(
                    prior.get(), actor, CREATE_ACTION,
                    requestFingerprint);
        }
        if (repository.findByFingerprint(
                        actor.principalId(),
                        parsed.fingerprintSha256())
                .isPresent()) {
            throw conflict(
                    "IDENTITY.MAINTENANCE_SSH_KEY_ALREADY_REGISTERED",
                    "该 SSH 公钥已经登记，撤销后也不能重新登记");
        }

        UUID keyUid = UUID.randomUUID();
        try {
            repository.insert(
                    keyUid,
                    actor.principalId(),
                    label,
                    parsed.canonicalPublicKey(),
                    parsed.fingerprintSha256());
        } catch (DataIntegrityViolationException conflict) {
            throw conflict(
                    "IDENTITY.MAINTENANCE_SSH_KEY_ALREADY_REGISTERED",
                    "该 SSH 公钥已经登记，撤销后也不能重新登记");
        }
        MaintenanceSshKeyRow created = repository.findByUid(
                        actor.principalId(), keyUid, false)
                .orElseThrow(() -> new IllegalStateException(
                        "created maintenance SSH key is unavailable"));
        appendAudit(
                operationUid,
                actor,
                CREATE_ACTION,
                requestFingerprint,
                null,
                created,
                null);
        return view(created);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public MaintenanceSshKeyView revokeOwnKey(
            UUID operationUid,
            UUID keyUid,
            RevokeMaintenanceSshKeyRequest request) {
        validateOperationUid(operationUid);
        if (keyUid == null || request == null
                || request.expectedVersion() == null) {
            throw invalid("密钥标识和资源版本不能为空");
        }
        if (request.expectedVersion() < 0) {
            throw invalid("资源版本不能小于零");
        }
        String reason = normalizeReason(request.reason());
        String targetIdentity = "maintenance-ssh-key:" + keyUid;
        TargetWebAuditRequestContext.describe(
                REVOKE_ACTION, targetIdentity);
        TargetWebActor actor = requireActivePlatformActor(true);
        String requestFingerprint = requestFingerprint(
                actor,
                REVOKE_ACTION,
                targetIdentity,
                new RevokeRequestFingerprint(
                        keyUid,
                        request.expectedVersion(),
                        reason));

        Optional<SuccessfulAudit> prior =
                auditPort.findSuccessful(operationUid);
        if (prior.isPresent()) {
            return replay(
                    prior.get(), actor, REVOKE_ACTION,
                    requestFingerprint);
        }

        MaintenanceSshKeyRow current = repository.findByUid(
                        actor.principalId(), keyUid, true)
                .orElseThrow(PlatformMaintenanceSshKeyService::notFound);
        if (current.revokedAt() != null) {
            throw conflict(
                    "IDENTITY.MAINTENANCE_SSH_KEY_ALREADY_REVOKED",
                    "该 SSH 公钥已经撤销");
        }
        if (current.version() != request.expectedVersion()) {
            throw versionConflict(current.version());
        }
        int affected = repository.revoke(
                current.id(), current.version(), reason);
        if (affected != 1) {
            throw versionConflict(current.version());
        }
        MaintenanceSshKeyRow revoked = repository.findByUid(
                        actor.principalId(), keyUid, false)
                .orElseThrow(() -> new IllegalStateException(
                        "revoked maintenance SSH key is unavailable"));
        appendAudit(
                operationUid,
                actor,
                REVOKE_ACTION,
                requestFingerprint,
                current,
                revoked,
                reason);
        return view(revoked);
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public Optional<ActivePlatformMaintenanceSshKey> resolveActive(
            UUID platformAdminUid,
            UUID maintenanceSshKeyUid) {
        Objects.requireNonNull(platformAdminUid, "platformAdminUid");
        Objects.requireNonNull(
                maintenanceSshKeyUid, "maintenanceSshKeyUid");
        return repository.findActive(
                        platformAdminUid, maintenanceSshKeyUid, true)
                .map(row -> new ActivePlatformMaintenanceSshKey(
                        platformAdminUid,
                        row.uid(),
                        row.label(),
                        row.publicKey(),
                        row.fingerprintSha256(),
                        referenceFactory.issue(row.id())));
    }

    private TargetWebActor requireActivePlatformActor(boolean forUpdate) {
        TargetWebActor actor = TargetWebActorContext.required();
        if (!actor.platform()) {
            throw forbidden();
        }
        PlatformAdminRow current = repository.findPlatformAdmin(
                        actor.principalId(),
                        actor.principalUid(),
                        forUpdate)
                .orElseThrow(PlatformMaintenanceSshKeyService::invalidSession);
        if (!current.enabled()
                || current.deletedAt() != null
                || current.authVersion() != actor.authVersion()) {
            throw invalidSession();
        }
        return actor;
    }

    private MaintenanceSshKeyView replay(
            SuccessfulAudit audit,
            TargetWebActor actor,
            String actionCode,
            String requestFingerprint) {
        JsonNode summary = readJson(audit.safeChangeSummaryJson());
        if (audit.actorKind() != AuditActorKind.PLATFORM_ADMIN
                || !Objects.equals(
                audit.platformAdminId(), actor.principalId())
                || !actionCode.equals(audit.actionCode())
                || !TARGET_TYPE.equals(audit.targetType())
                || !requestFingerprint.equals(
                summary.path("fingerprint").asText())) {
            throw idempotencyConflict();
        }
        UUID keyUid;
        try {
            keyUid = UUID.fromString(audit.targetStableKey());
        } catch (RuntimeException invalidAuditTarget) {
            throw idempotencyConflict();
        }
        return repository.findByUid(
                        actor.principalId(), keyUid, false)
                .map(PlatformMaintenanceSshKeyService::view)
                .orElseThrow(PlatformMaintenanceSshKeyService::notFound);
    }

    private void appendAudit(
            UUID operationUid,
            TargetWebActor actor,
            String actionCode,
            String requestFingerprint,
            MaintenanceSshKeyRow before,
            MaintenanceSshKeyRow after,
            String reason) {
        String safeSummary = writeJson(new SafeChange(
                requestFingerprint,
                snapshot(before),
                snapshot(after),
                Map.of("reasonPresent", reason != null)));
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
                TARGET_TYPE,
                after.uid().toString(),
                "WEB",
                "SUCCEEDED",
                actor.sessionUid(),
                reason,
                safeSummary,
                Instant.now()));
    }

    private String requestFingerprint(
            TargetWebActor actor,
            String actionCode,
            String targetIdentity,
            Object normalizedRequest) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            digest.update(actor.principalUid().toString()
                    .getBytes(StandardCharsets.UTF_8));
            digest.update((byte) 0);
            digest.update(actionCode.getBytes(StandardCharsets.UTF_8));
            digest.update((byte) 0);
            digest.update(targetIdentity.getBytes(StandardCharsets.UTF_8));
            digest.update((byte) 0);
            digest.update(objectMapper.writeValueAsBytes(normalizedRequest));
            return HexFormat.of().formatHex(digest.digest());
        } catch (NoSuchAlgorithmException unavailable) {
            throw new IllegalStateException(
                    "SHA-256 is unavailable", unavailable);
        }
    }

    private String writeJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (Exception encodingFailure) {
            throw new IllegalStateException(
                    "safe audit summary cannot be encoded",
                    encodingFailure);
        }
    }

    private JsonNode readJson(String value) {
        try {
            return objectMapper.readTree(value);
        } catch (Exception decodingFailure) {
            throw new IllegalStateException(
                    "safe audit summary cannot be decoded",
                    decodingFailure);
        }
    }

    private static MaintenanceSshKeyView view(MaintenanceSshKeyRow row) {
        return new MaintenanceSshKeyView(
                row.uid(),
                row.label(),
                row.publicKey(),
                row.fingerprintSha256(),
                row.revokedAt() == null ? "ACTIVE" : "REVOKED",
                row.version(),
                row.createdAt(),
                row.updatedAt(),
                row.revokedAt(),
                row.revokedReason());
    }

    private static KeyAuditSnapshot snapshot(MaintenanceSshKeyRow row) {
        return row == null ? null : new KeyAuditSnapshot(
                row.uid(),
                row.label(),
                row.fingerprintSha256(),
                row.revokedAt() == null ? "ACTIVE" : "REVOKED",
                row.version());
    }

    private static String normalizeLabel(String value) {
        String normalized = value == null ? "" : value.trim();
        if (normalized.isEmpty() || normalized.length() > 100) {
            throw invalid("密钥标签不能为空且不能超过 100 个字符");
        }
        return normalized;
    }

    private static String normalizeReason(String value) {
        String normalized = value == null ? "" : value.trim();
        if (normalized.isEmpty() || normalized.length() > 500) {
            throw invalid("撤销原因不能为空且不能超过 500 个字符");
        }
        return normalized;
    }

    private static void validateOperationUid(UUID operationUid) {
        if (operationUid == null || operationUid.version() != 4) {
            throw invalid("Idempotency-Key 必须是 UUIDv4");
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
                "仅平台管理员可以管理自己的维护 SSH 公钥");
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
                "维护 SSH 公钥不存在");
    }

    private static TargetApiException conflict(
            String code,
            String message) {
        return new TargetApiException(409, code, message);
    }

    private static TargetApiException idempotencyConflict() {
        return conflict(
                "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                "相同操作标识已绑定到不同请求");
    }

    private static TargetApiException versionConflict(long currentVersion) {
        return new TargetApiException(
                409,
                "COMMON.VERSION_CONFLICT",
                "资源版本已变化，请重新查询",
                false,
                Map.of("currentVersion", currentVersion));
    }

    private record CreateRequestFingerprint(
            String label,
            String publicKey,
            String fingerprintSha256) {
    }

    private record RevokeRequestFingerprint(
            UUID keyUid,
            long expectedVersion,
            String reason) {
    }

    private record KeyAuditSnapshot(
            UUID keyUid,
            String label,
            String fingerprintSha256,
            String status,
            long version) {
    }

    private record SafeChange(
            String fingerprint,
            KeyAuditSnapshot before,
            KeyAuditSnapshot after,
            Map<String, Object> metadata) {
    }
}
