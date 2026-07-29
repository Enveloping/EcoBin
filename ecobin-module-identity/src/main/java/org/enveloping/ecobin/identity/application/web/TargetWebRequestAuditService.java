package org.enveloping.ecobin.identity.application.web;

import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.web.TargetWebAuditRequestContext.Descriptor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.UUID;

@Service
public class TargetWebRequestAuditService {

    private static final String PLATFORM_TENANTS =
            "/api/v1/web/platform/tenants";
    private static final String PLATFORM_LOGIN =
            "/api/v1/web/platform/auth/sessions";
    private static final String STAFF_LOGIN =
            "/api/v1/web/auth/sessions";

    private final JdbcTemplate jdbc;
    private final AuditPort auditPort;
    private final ObjectMapper objectMapper;

    public TargetWebRequestAuditService(
            JdbcTemplate jdbc,
            AuditPort auditPort,
            ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.auditPort = auditPort;
        this.objectMapper = objectMapper;
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void record(
            String method,
            String path,
            int status,
            String operationUidHeader,
            TargetWebActor actor,
            Descriptor descriptor) {
        boolean privilegedRead = status < 400
                && "GET".equals(method)
                && actor != null
                && actor.platform()
                && (PLATFORM_TENANTS.equals(path)
                || path.startsWith(PLATFORM_TENANTS + "/"));
        if (!privilegedRead && status < 400) {
            return;
        }

        Target target = resolveTarget(path, actor);
        UUID operationUid = privilegedRead
                ? UUID.randomUUID()
                : validOperationUid(operationUidHeader);
        String result = privilegedRead
                ? "SUCCEEDED"
                : status >= 500 ? "FAILED" : "DENIED";
        AuditActorKind actorKind = actor == null
                ? AuditActorKind.UNAUTHENTICATED
                : actor.platform()
                ? AuditActorKind.PLATFORM_ADMIN
                : AuditActorKind.STAFF_ACCOUNT;
        String actionCode = !privilegedRead && descriptor != null
                ? descriptor.actionCode()
                : actionCode(path, privilegedRead);
        if (!privilegedRead && actor != null && descriptor != null) {
            target = target.withDescriptor(descriptor);
        }
        Map<String, Object> safeSummary = Map.of(
                "httpMethod", method,
                "httpStatus", status,
                "pathCategory", target.pathCategory(),
                "requestPathSha256", sha256(method + "\0" + path));

        auditPort.append(new AuditEntry(
                UUID.randomUUID(),
                UUID.randomUUID(),
                operationUid,
                target.scopeKind(),
                target.tenantId(),
                target.organizationId(),
                actorKind,
                actor != null && actor.platform()
                        ? actor.principalId() : null,
                actor != null && !actor.platform()
                        ? actor.principalId() : null,
                null,
                null,
                actor == null ? null : actor.displayName(),
                actionCode,
                target.targetType(),
                target.stableKey(),
                isLogin(path) ? "SECURITY_ENTRY" : "WEB",
                result,
                actor == null ? null : actor.sessionUid(),
                null,
                objectMapper.writeValueAsString(safeSummary),
                Instant.now()));
    }

    private Target resolveTarget(String path, TargetWebActor actor) {
        if (actor == null) {
            return new Target(
                    AuditScopeKind.UNRESOLVED,
                    null,
                    null,
                    isLogin(path) ? "authentication" : "web-request",
                    isLogin(path) ? loginTarget(path) : pathDigest(path),
                    isLogin(path) ? "authentication" : "unresolved");
        }
        if (actor.platform()) {
            if (PLATFORM_TENANTS.equals(path)) {
                return new Target(
                        AuditScopeKind.PLATFORM,
                        null,
                        null,
                        "tenant-directory",
                        "tenants",
                        "tenant-directory");
            }
            if (path.startsWith(PLATFORM_TENANTS + "/")) {
                return resolvePlatformTenantTarget(path);
            }
            return new Target(
                    AuditScopeKind.PLATFORM,
                    null,
                    null,
                    "platform-request",
                    pathDigest(path),
                    "platform");
        }
        return resolveStaffTarget(path, actor);
    }

    private Target resolvePlatformTenantTarget(String path) {
        List<String> segments = segments(path);
        int tenantIndex = segments.indexOf("tenants");
        if (tenantIndex < 0 || tenantIndex + 1 >= segments.size()) {
            return unresolved(path);
        }
        String tenantCode = segments.get(tenantIndex + 1);
        Long tenantId = jdbc.query("""
                        SELECT id
                        FROM iam_tenant
                        WHERE tenant_code = ?
                        """,
                (rs, ignored) -> rs.getLong("id"),
                tenantCode).stream().findFirst().orElse(null);
        if (tenantId == null) {
            return unresolved(path);
        }
        int organizationIndex = segments.indexOf("organizations");
        if (organizationIndex >= 0
                && organizationIndex + 1 < segments.size()) {
            String organizationCode = segments.get(organizationIndex + 1);
            Long organizationId = organizationId(
                    tenantId, organizationCode);
            if (organizationId != null) {
                return new Target(
                        AuditScopeKind.ORGANIZATION,
                        tenantId,
                        organizationId,
                        targetType(segments),
                        stableKey(tenantCode, organizationCode, segments),
                        "tenant-organization");
            }
        }
        return new Target(
                AuditScopeKind.TENANT,
                tenantId,
                null,
                targetType(segments),
                stableKey(tenantCode, null, segments),
                "tenant");
    }

    private Target resolveStaffTarget(
            String path,
            TargetWebActor actor) {
        List<String> segments = segments(path);
        int organizationIndex = segments.indexOf("organizations");
        if (organizationIndex >= 0
                && organizationIndex + 1 < segments.size()) {
            String organizationCode = segments.get(organizationIndex + 1);
            Long organizationId = organizationId(
                    actor.tenantId(), organizationCode);
            if (organizationId != null) {
                return new Target(
                        AuditScopeKind.ORGANIZATION,
                        actor.tenantId(),
                        organizationId,
                        targetType(segments),
                        stableKey(
                                actor.tenantCode(),
                                organizationCode,
                                segments),
                        "organization");
            }
        }
        return new Target(
                AuditScopeKind.TENANT,
                actor.tenantId(),
                null,
                targetType(segments),
                stableKey(actor.tenantCode(), null, segments),
                "tenant");
    }

    private Long organizationId(long tenantId, String organizationCode) {
        return jdbc.query("""
                        SELECT id
                        FROM iam_organization
                        WHERE tenant_id = ?
                          AND organization_code = ?
                        """,
                (rs, ignored) -> rs.getLong("id"),
                tenantId,
                organizationCode).stream().findFirst().orElse(null);
    }

    private static String targetType(List<String> segments) {
        if (segments.contains("staff-memberships")) {
            return "organization-membership";
        }
        if (segments.contains("staff-accounts")) {
            return "staff-account";
        }
        if (segments.contains("organizations")) {
            return "organization";
        }
        return "tenant";
    }

    private static String stableKey(
            String tenantCode,
            String organizationCode,
            List<String> segments) {
        StringBuilder key = new StringBuilder(tenantCode);
        if (organizationCode != null) {
            key.append(':').append(organizationCode);
        }
        int staffIndex = segments.indexOf("staff-accounts");
        if (staffIndex >= 0 && staffIndex + 1 < segments.size()) {
            String candidate = segments.get(staffIndex + 1);
            if (!"current".equals(candidate)) {
                key.append(':').append(candidate);
            }
        }
        int membershipIndex = segments.indexOf("staff-memberships");
        if (membershipIndex >= 0
                && membershipIndex + 1 < segments.size()) {
            key.append(':').append(segments.get(membershipIndex + 1));
        }
        return key.substring(0, Math.min(key.length(), 160));
    }

    private static String actionCode(
            String path,
            boolean privilegedRead) {
        if (PLATFORM_LOGIN.equals(path)) {
            return "identity.auth.platform.login";
        }
        if (STAFF_LOGIN.equals(path)) {
            return "identity.auth.staff.login";
        }
        return privilegedRead
                ? "identity.platform.privileged-read"
                : "identity.web.request";
    }

    private static boolean isLogin(String path) {
        return PLATFORM_LOGIN.equals(path) || STAFF_LOGIN.equals(path);
    }

    private static String loginTarget(String path) {
        return PLATFORM_LOGIN.equals(path) ? "platform" : "staff";
    }

    private static Target unresolved(String path) {
        return new Target(
                AuditScopeKind.UNRESOLVED,
                null,
                null,
                "web-request",
                pathDigest(path),
                "unresolved");
    }

    private static List<String> segments(String path) {
        return java.util.Arrays.stream(path.split("/"))
                .filter(segment -> !segment.isBlank())
                .toList();
    }

    private static UUID validOperationUid(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        try {
            UUID uid = UUID.fromString(value);
            return uid.version() == 4 ? uid : null;
        } catch (IllegalArgumentException ignored) {
            return null;
        }
    }

    private static String pathDigest(String path) {
        return "path-sha256:" + sha256(path);
    }

    private static String sha256(String value) {
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(
                            value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
    }

    private record Target(
            AuditScopeKind scopeKind,
            Long tenantId,
            Long organizationId,
            String targetType,
            String stableKey,
            String pathCategory) {

        private Target withDescriptor(Descriptor descriptor) {
            String stableTarget = descriptor.targetIdentity();
            if (stableTarget.length() > 160) {
                stableTarget = stableTarget.substring(0, 160);
            }
            String type = descriptor.actionCode().contains("membership")
                    ? "organization-membership"
                    : descriptor.actionCode().contains("staff")
                    ? "staff-account"
                    : descriptor.actionCode().contains("organization")
                    ? "organization"
                    : "tenant";
            return new Target(
                    scopeKind,
                    tenantId,
                    organizationId,
                    type,
                    stableTarget,
                    pathCategory);
        }
    }
}
