package org.enveloping.ecobin.recycling.application.clean;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.DeliveryScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.query.DeliveryScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedDeliveryScope;
import org.enveloping.ecobin.recycling.web.v1.CleanRecordModels.EditCleanRecordRequest;
import org.enveloping.ecobin.recycling.web.v1.CleanRecordModels.EditCleanRecordResult;
import org.enveloping.ecobin.recycling.web.v1.CleanRecordModels.EffectiveWeightEdit;
import org.enveloping.ecobin.recycling.web.v1.CleanRecordModels.RemarkEdit;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.security.MessageDigest;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/** 可信工作人员清运记录的直接修改和只追加留痕事务。 */
@Service
public class CleanRecordEditService {

    private final JdbcTemplate jdbc;
    private final DeliveryScopeAuthorizationPort authorization;
    private final ObjectMapper objectMapper;

    public CleanRecordEditService(
            JdbcTemplate jdbc,
            DeliveryScopeAuthorizationPort authorization,
            ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.authorization = authorization;
        this.objectMapper = objectMapper;
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public EditCleanRecordResult edit(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String cleanRecordNo,
            UUID idempotencyKey,
            EditCleanRecordRequest request) {
        requireUuidV4(idempotencyKey);
        String recordNo = recordNo(cleanRecordNo);
        NormalizedEdit normalized = normalize(request);
        byte[] requestHash = requestHash(recordNo, normalized);

        AuthorizedDeliveryScope authorized = authorization.authorize(
                new DeliveryScopeAuthorizationQuery(
                        platformPath,
                        tenantCode,
                        organizationCode));
        if (!authorized.cleanEdit()) {
            throw new TargetApiException(
                    403,
                    "AUTH.CAPABILITY_REQUIRED",
                    "当前账号没有清运记录修改能力");
        }
        ActorScope scope = authorized.persistenceRef().withScopeOnce(
                ActorScope::new);

        lockScope(scope);
        ExistingChange existing = existingChange(
                scope,
                idempotencyKey);
        if (existing != null) {
            if (!existing.recordNo().equals(recordNo)
                    || !MessageDigest.isEqual(
                            existing.requestHash(),
                            requestHash)) {
                throw idempotencyReused();
            }
            return result(existing);
        }

        CurrentRecord current = lockRecord(scope, recordNo);
        if (current.version() != normalized.expectedVersion()) {
            throw versionConflict(
                    normalized.expectedVersion(),
                    current.version());
        }
        AppliedValues after = apply(current, normalized);
        LocalDateTime now = databaseNow();
        UUID changeUid = UUID.randomUUID();
        long nextVersion = current.version() + 1;
        String actorKind = scope.platformAdminId() != null
                ? "PLATFORM_ADMIN"
                : "STAFF";

        int inserted = jdbc.update("""
                        INSERT INTO rec_clean_record_change (
                            change_uid,
                            tenant_id, organization_id,
                            clean_record_id,
                            from_version, to_version,
                            before_effective_removed_net_weight_g,
                            before_effective_weight_source,
                            before_record_remark,
                            after_effective_removed_net_weight_g,
                            after_effective_weight_source,
                            after_record_remark,
                            reason, actor_kind,
                            platform_admin_id, staff_account_id,
                            idempotency_key, request_sha256,
                            changed_at, created_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                changeUid.toString(),
                scope.tenantId(),
                scope.organizationId(),
                current.id(),
                current.version(),
                nextVersion,
                current.effectiveWeight(),
                current.effectiveSource(),
                current.remark(),
                after.effectiveWeight(),
                after.effectiveSource(),
                after.remark(),
                normalized.reason(),
                actorKind,
                scope.platformAdminId(),
                scope.staffAccountId(),
                idempotencyKey.toString(),
                requestHash,
                now,
                now);
        if (inserted != 1) {
            throw new IllegalStateException(
                    "clean record change insert affected "
                            + inserted + " rows");
        }
        int updated = jdbc.update("""
                        UPDATE rec_clean_record
                        SET effective_removed_net_weight_g = ?,
                            effective_weight_source = ?,
                            record_remark = ?,
                            lock_version = ?,
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND id = ?
                          AND lock_version = ?
                        """,
                after.effectiveWeight(),
                after.effectiveSource(),
                after.remark(),
                nextVersion,
                now,
                scope.tenantId(),
                scope.organizationId(),
                current.id(),
                current.version());
        if (updated != 1) {
            throw new IllegalStateException(
                    "locked clean record version changed unexpectedly");
        }
        return new EditCleanRecordResult(
                recordNo,
                nextVersion,
                kg(after.effectiveWeight()),
                after.effectiveSource(),
                after.remark(),
                instant(now),
                changeUid);
    }

    private void lockScope(ActorScope scope) {
        Long locked = jdbc.query("""
                        SELECT organization_id
                        FROM rec_organization_clean_record_counter
                        WHERE tenant_id = ?
                          AND organization_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("organization_id"),
                scope.tenantId(),
                scope.organizationId()).stream().findFirst()
                .orElseThrow(CleanRecordEditService::notFound);
        if (locked != scope.organizationId()) {
            throw new IllegalStateException(
                    "clean record edit scope lock is inconsistent");
        }
    }

    private ExistingChange existingChange(
            ActorScope scope,
            UUID idempotencyKey) {
        return jdbc.query("""
                        SELECT record.clean_record_no,
                               change_row.change_uid,
                               change_row.to_version,
                               change_row.after_effective_removed_net_weight_g,
                               change_row.after_effective_weight_source,
                               change_row.after_record_remark,
                               change_row.request_sha256,
                               change_row.changed_at
                        FROM rec_clean_record_change change_row
                        JOIN rec_clean_record record
                          ON record.id = change_row.clean_record_id
                        WHERE change_row.tenant_id = ?
                          AND change_row.organization_id = ?
                          AND change_row.idempotency_key = ?
                        """,
                (rs, ignored) -> new ExistingChange(
                        rs.getString("clean_record_no"),
                        UUID.fromString(rs.getString("change_uid")),
                        rs.getLong("to_version"),
                        nullableLong(rs.getObject(
                                "after_effective_removed_net_weight_g")),
                        rs.getString("after_effective_weight_source"),
                        rs.getString("after_record_remark"),
                        rs.getBytes("request_sha256"),
                        rs.getObject("changed_at", LocalDateTime.class)),
                scope.tenantId(),
                scope.organizationId(),
                idempotencyKey.toString()).stream().findFirst()
                .orElse(null);
    }

    private CurrentRecord lockRecord(
            ActorScope scope,
            String recordNo) {
        return jdbc.query("""
                        SELECT id, effective_removed_net_weight_g,
                               effective_weight_source,
                               record_remark, lock_version
                        FROM rec_clean_record
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND clean_record_no = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new CurrentRecord(
                        rs.getLong("id"),
                        nullableLong(rs.getObject(
                                "effective_removed_net_weight_g")),
                        rs.getString("effective_weight_source"),
                        rs.getString("record_remark"),
                        rs.getLong("lock_version")),
                scope.tenantId(),
                scope.organizationId(),
                recordNo).stream().findFirst().orElseThrow(
                CleanRecordEditService::notFound);
    }

    private static AppliedValues apply(
            CurrentRecord current,
            NormalizedEdit edit) {
        Long weight = current.effectiveWeight();
        String source = current.effectiveSource();
        String remark = current.remark();
        if (edit.weight().submitted()) {
            if ("SET".equals(edit.weight().action())) {
                weight = edit.weight().grams();
                source = "MANUAL_SET";
            } else {
                weight = null;
                source = "MANUAL_CLEARED";
            }
        }
        if (edit.remark().submitted()) {
            remark = "SET".equals(edit.remark().action())
                    ? edit.remark().value()
                    : null;
        }
        return new AppliedValues(weight, source, remark);
    }

    private NormalizedEdit normalize(EditCleanRecordRequest request) {
        if (request == null || request.expectedVersion() == null
                || request.expectedVersion() < 1) {
            throw validation(
                    "expectedVersion",
                    "expectedVersion 必须是正整数");
        }
        NormalizedWeight weight = weight(
                request.effectiveRemovedNetWeight());
        NormalizedRemark remark = remark(request.recordRemark());
        if (!weight.submitted() && !remark.submitted()) {
            throw validation(
                    "body",
                    "至少提交 effectiveRemovedNetWeight 或 recordRemark");
        }
        String reason = trimRequired(
                request.reason(), "reason", 500);
        return new NormalizedEdit(
                request.expectedVersion(),
                weight,
                remark,
                reason);
    }

    private static NormalizedWeight weight(EffectiveWeightEdit edit) {
        if (edit == null) {
            return NormalizedWeight.omitted();
        }
        String action = action(edit.action(),
                "effectiveRemovedNetWeight.action");
        if ("CLEAR".equals(action)) {
            if (edit.valueKg() != null
                    && !edit.valueKg().isBlank()) {
                throw validation(
                        "effectiveRemovedNetWeight.valueKg",
                        "重量 CLEAR 时不能提交 valueKg");
            }
            return new NormalizedWeight(true, action, null, null);
        }
        String value = edit.valueKg() == null
                ? null
                : edit.valueKg().trim();
        if (value == null
                || !value.matches(
                        "(?:0|[1-9][0-9]{0,2}|1000)(?:\\.[0-9]{1,2})?")) {
            throw validation(
                    "effectiveRemovedNetWeight.valueKg",
                    "重量必须是 0.00 到 1000.00 kg，最多两位小数");
        }
        BigDecimal kilograms = new BigDecimal(value);
        if (kilograms.compareTo(BigDecimal.valueOf(1000)) > 0) {
            throw validation(
                    "effectiveRemovedNetWeight.valueKg",
                    "重量不能超过 1000.00 kg");
        }
        long grams;
        try {
            grams = kilograms.movePointRight(3).longValueExact();
        } catch (ArithmeticException exception) {
            throw validation(
                    "effectiveRemovedNetWeight.valueKg",
                    "重量无法精确换算为克");
        }
        return new NormalizedWeight(
                true,
                action,
                grams,
                kilograms.stripTrailingZeros().toPlainString());
    }

    private static NormalizedRemark remark(RemarkEdit edit) {
        if (edit == null) {
            return NormalizedRemark.omitted();
        }
        String action = action(edit.action(), "recordRemark.action");
        if ("CLEAR".equals(action)) {
            if (edit.value() != null && !edit.value().isBlank()) {
                throw validation(
                        "recordRemark.value",
                        "备注 CLEAR 时不能提交 value");
            }
            return new NormalizedRemark(true, action, null);
        }
        return new NormalizedRemark(
                true,
                action,
                trimRequired(edit.value(), "recordRemark.value", 500));
    }

    private static String action(String value, String field) {
        if (value == null || value.isBlank()) {
            throw validation(field, field + " 必填");
        }
        String normalized = value.trim().toUpperCase();
        if (!"SET".equals(normalized)
                && !"CLEAR".equals(normalized)) {
            throw validation(field, field + " 只能是 SET 或 CLEAR");
        }
        return normalized;
    }

    private static String trimRequired(
            String value,
            String field,
            int maximum) {
        if (value == null || value.isBlank()) {
            throw validation(field, field + " 必填");
        }
        String normalized = value.trim();
        if (normalized.length() > maximum) {
            throw validation(
                    field,
                    field + " 长度不能超过 " + maximum);
        }
        return normalized;
    }

    private byte[] requestHash(
            String recordNo,
            NormalizedEdit edit) {
        try {
            Map<String, Object> canonical = new LinkedHashMap<>();
            canonical.put("cleanRecordNo", recordNo);
            canonical.put("expectedVersion", edit.expectedVersion());
            canonical.put("weightSubmitted", edit.weight().submitted());
            canonical.put("weightAction", marker(edit.weight().action()));
            canonical.put(
                    "weightValueKg",
                    marker(edit.weight().canonicalValue()));
            canonical.put("remarkSubmitted", edit.remark().submitted());
            canonical.put("remarkAction", marker(edit.remark().action()));
            canonical.put("remarkValue", marker(edit.remark().value()));
            canonical.put("reason", edit.reason());
            return MessageDigest.getInstance("SHA-256").digest(
                    objectMapper.writeValueAsBytes(canonical));
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "clean record edit request cannot be hashed",
                    exception);
        }
    }

    private LocalDateTime databaseNow() {
        LocalDateTime now = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)",
                LocalDateTime.class);
        if (now == null) {
            throw new IllegalStateException(
                    "database time is unavailable");
        }
        return now;
    }

    private static EditCleanRecordResult result(
            ExistingChange existing) {
        return new EditCleanRecordResult(
                existing.recordNo(),
                existing.version(),
                kg(existing.effectiveWeight()),
                existing.effectiveSource(),
                existing.remark(),
                instant(existing.changedAt()),
                existing.changeUid());
    }

    private static void requireUuidV4(UUID value) {
        if (value == null || value.version() != 4 || value.variant() != 2) {
            throw validation(
                    "Idempotency-Key",
                    "Idempotency-Key 必须是 UUIDv4");
        }
    }

    private static String recordNo(String value) {
        if (value == null
                || !value.trim().matches("[A-Za-z0-9_-]{8,64}")) {
            throw notFound();
        }
        return value.trim();
    }

    private static Object marker(Object value) {
        return value == null ? "<null>" : value;
    }

    private static Long nullableLong(Object value) {
        return value == null ? null : ((Number) value).longValue();
    }

    private static String kg(Long grams) {
        if (grams == null) {
            return null;
        }
        BigDecimal value = BigDecimal.valueOf(grams, 3)
                .stripTrailingZeros();
        if (value.scale() < 2) {
            value = value.setScale(2);
        }
        return value.toPlainString();
    }

    private static Instant instant(LocalDateTime value) {
        return value.toInstant(ZoneOffset.UTC);
    }

    private static TargetApiException versionConflict(
            long expected,
            long actual) {
        return new TargetApiException(
                409,
                "CLEAN.RECORD_VERSION_CONFLICT",
                "清运记录已经被其他工作人员修改，请刷新后重试",
                false,
                Map.of(
                        "expectedVersion", expected,
                        "currentVersion", actual));
    }

    private static TargetApiException idempotencyReused() {
        return new TargetApiException(
                409,
                "IDEMPOTENCY.KEY_REUSED",
                "该幂等键已经用于另一份清运记录修改请求");
    }

    private static TargetApiException validation(
            String field,
            String message) {
        return new TargetApiException(
                400,
                "COMMON.VALIDATION_FAILED",
                message,
                false,
                Map.of("field", field));
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404,
                "RESOURCE.NOT_FOUND",
                "资源不存在");
    }

    private record ActorScope(
            long tenantId,
            long organizationId,
            Long platformAdminId,
            Long staffAccountId) {
    }

    private record CurrentRecord(
            long id,
            Long effectiveWeight,
            String effectiveSource,
            String remark,
            long version) {
    }

    private record AppliedValues(
            Long effectiveWeight,
            String effectiveSource,
            String remark) {
    }

    private record ExistingChange(
            String recordNo,
            UUID changeUid,
            long version,
            Long effectiveWeight,
            String effectiveSource,
            String remark,
            byte[] requestHash,
            LocalDateTime changedAt) {

        ExistingChange {
            requestHash = Arrays.copyOf(requestHash, requestHash.length);
        }

        @Override
        public byte[] requestHash() {
            return Arrays.copyOf(requestHash, requestHash.length);
        }
    }

    private record NormalizedEdit(
            long expectedVersion,
            NormalizedWeight weight,
            NormalizedRemark remark,
            String reason) {
    }

    private record NormalizedWeight(
            boolean submitted,
            String action,
            Long grams,
            String canonicalValue) {

        static NormalizedWeight omitted() {
            return new NormalizedWeight(false, null, null, null);
        }
    }

    private record NormalizedRemark(
            boolean submitted,
            String action,
            String value) {

        static NormalizedRemark omitted() {
            return new NormalizedRemark(false, null, null);
        }
    }
}
