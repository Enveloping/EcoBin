package org.enveloping.ecobin.operations.application.governance;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.HexFormat;
import java.util.UUID;

@Service
public class GovernanceIdempotencyService {

    static final String CLAIM_SQL = """
            INSERT INTO ops_governance_idempotency (
                operation_uid, actor_kind, actor_uid, scope_sha256,
                action_code, target_type, target_stable_key,
                request_sha256, status, result_resource_uid,
                result_state, result_version, created_at,
                completed_at, updated_at
            ) VALUES (?, ?, ?, UNHEX(?), ?, ?, ?, UNHEX(?),
                      'IN_PROGRESS', NULL, NULL, NULL, ?, NULL, ?)
            ON DUPLICATE KEY UPDATE
                updated_at = updated_at
            """;

    private final JdbcTemplate jdbc;

    public GovernanceIdempotencyService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public Claim claim(Request request) {
        GovernanceIdempotency.requireVersionFour(request.operationUid());
        LocalDateTime now = databaseNow();
        jdbc.update(CLAIM_SQL,
                request.operationUid().toString(), request.actorKind(),
                request.actorUid().toString(), request.scopeDigest(),
                request.actionCode(), request.targetType(),
                request.targetStableKey(), request.requestDigest(), now, now);
        Row row = jdbc.query("""
                        SELECT actor_kind, actor_uid,
                               LOWER(HEX(scope_sha256)) scope_sha256,
                               action_code, target_type, target_stable_key,
                               LOWER(HEX(request_sha256)) request_sha256,
                               status, result_resource_uid,
                               result_state, result_version
                        FROM ops_governance_idempotency
                        WHERE operation_uid = ? FOR UPDATE
                        """,
                (rs, ignored) -> new Row(
                        rs.getString("actor_kind"),
                        UUID.fromString(rs.getString("actor_uid")),
                        rs.getString("scope_sha256"),
                        rs.getString("action_code"),
                        rs.getString("target_type"),
                        rs.getString("target_stable_key"),
                        rs.getString("request_sha256"),
                        rs.getString("status"),
                        uuid(rs.getString("result_resource_uid")),
                        rs.getString("result_state"),
                        (Long) rs.getObject("result_version")),
                request.operationUid().toString()).stream().findFirst()
                .orElseThrow(() -> new IllegalStateException(
                        "idempotency claim disappeared"));
        if (!row.matches(request)) {
            throw conflict();
        }
        if ("SUCCEEDED".equals(row.status())) {
            return new Claim(true, new Result(
                    row.resultResourceUid(), row.resultState(),
                    row.resultVersion()));
        }
        return new Claim(false, null);
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public void succeed(UUID operationUid, Result result) {
        LocalDateTime now = databaseNow();
        int updated = jdbc.update("""
                        UPDATE ops_governance_idempotency
                        SET status = 'SUCCEEDED', result_resource_uid = ?,
                            result_state = ?, result_version = ?,
                            completed_at = ?, updated_at = ?
                        WHERE operation_uid = ? AND status = 'IN_PROGRESS'
                        """,
                result.resourceUid().toString(), result.state(),
                result.version(), now, now, operationUid.toString());
        if (updated != 1) {
            throw new IllegalStateException(
                    "idempotency claim was not completed exactly once");
        }
    }

    private LocalDateTime databaseNow() {
        LocalDateTime value = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        if (value == null) {
            throw new IllegalStateException("database time unavailable");
        }
        return value;
    }

    private static UUID uuid(String value) {
        return value == null ? null : UUID.fromString(value);
    }

    private static TargetApiException conflict() {
        return new TargetApiException(
                409, "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                "幂等键已经用于其他请求或权限范围");
    }

    public record Request(
            UUID operationUid,
            String actorKind,
            UUID actorUid,
            String scopeDigest,
            String actionCode,
            String targetType,
            String targetStableKey,
            String requestDigest) {

        public Request {
            if (actorUid == null || actorKind == null || scopeDigest == null
                    || actionCode == null || targetType == null
                    || targetStableKey == null || requestDigest == null) {
                throw new IllegalArgumentException(
                        "idempotency request is incomplete");
            }
            HexFormat.of().parseHex(scopeDigest);
            HexFormat.of().parseHex(requestDigest);
        }
    }

    public record Claim(boolean replay, Result result) { }

    public record Result(UUID resourceUid, String state, long version) { }

    private record Row(
            String actorKind,
            UUID actorUid,
            String scopeDigest,
            String actionCode,
            String targetType,
            String targetStableKey,
            String requestDigest,
            String status,
            UUID resultResourceUid,
            String resultState,
            Long resultVersion) {

        boolean matches(Request request) {
            return actorKind.equals(request.actorKind())
                    && actorUid.equals(request.actorUid())
                    && scopeDigest.equals(request.scopeDigest())
                    && actionCode.equals(request.actionCode())
                    && targetType.equals(request.targetType())
                    && targetStableKey.equals(request.targetStableKey())
                    && requestDigest.equals(request.requestDigest());
        }
    }
}
