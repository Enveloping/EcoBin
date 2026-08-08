package org.enveloping.ecobin.recycling.application.bag;

import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.audit.SuccessfulAudit;
import org.enveloping.ecobin.framework.web.TargetWebAuditRequestContext;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.ManagementScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.query.ManagementScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedManagementScope;
import org.enveloping.ecobin.recycling.application.bag.Eb1BagCodeService.IssuedBagCode;
import org.enveloping.ecobin.recycling.web.v1.BagLabelModels.BagLabelBatchSummary;
import org.enveloping.ecobin.recycling.web.v1.BagLabelModels.BagLabelBatchView;
import org.enveloping.ecobin.recycling.web.v1.BagLabelModels.BagLabelItemView;
import org.enveloping.ecobin.recycling.web.v1.BagLabelModels.CreateBagLabelBatchRequest;
import org.enveloping.ecobin.recycling.web.v1.BagLabelModels.PageData;
import org.enveloping.ecobin.recycling.web.v1.BagLabelModels.PlatformAdminSummary;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.PreparedStatementCreator;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.jdbc.support.KeyHolder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.sql.PreparedStatement;
import java.sql.Statement;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

/** Platform-only issuance and disposable printing history for EB1 labels. */
@Service
public class PlatformBagLabelApplicationService {

    private static final String GENERATE_ACTION =
            "recycling.bag-label-batch.generate";
    private static final String DELETE_ACTION =
            "recycling.bag-label-batch.delete";
    private static final String TARGET_TYPE = "BAG_LABEL_BATCH";
    private static final int MAX_PAGE_SIZE = 100;

    private final JdbcTemplate jdbc;
    private final Eb1BagCodeService bagCodes;
    private final ManagementScopeAuthorizationPort managementScopes;
    private final AuditPort audit;
    private final ObjectMapper objectMapper;

    public PlatformBagLabelApplicationService(
            JdbcTemplate jdbc,
            Eb1BagCodeService bagCodes,
            ManagementScopeAuthorizationPort managementScopes,
            AuditPort audit,
            ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.bagCodes = bagCodes;
        this.managementScopes = managementScopes;
        this.audit = audit;
        this.objectMapper = objectMapper;
    }

    @Transactional(readOnly = true)
    public PageData<BagLabelBatchSummary> list(
            int requestedPage,
            int requestedPageSize) {
        requirePlatform();
        int page = Math.max(1, requestedPage);
        int pageSize = Math.max(
                1, Math.min(MAX_PAGE_SIZE, requestedPageSize));
        Long total = jdbc.queryForObject(
                "SELECT COUNT(*) FROM rec_bag_label_batch",
                Long.class);
        List<BagLabelBatchSummary> items = jdbc.query("""
                        SELECT batch.batch_uid, batch.key_id,
                               batch.label_count, batch.created_at,
                               admin.platform_admin_uid,
                               admin.display_name
                        FROM rec_bag_label_batch batch
                        JOIN iam_platform_admin admin
                          ON admin.id = batch.created_by_platform_admin_id
                        ORDER BY batch.created_at DESC, batch.id DESC
                        LIMIT ? OFFSET ?
                        """,
                (rs, ignored) -> summary(
                        rs.getString("batch_uid"),
                        rs.getString("key_id"),
                        rs.getInt("label_count"),
                        rs.getString("platform_admin_uid"),
                        rs.getString("display_name"),
                        rs.getObject("created_at", LocalDateTime.class)),
                pageSize,
                (page - 1L) * pageSize);
        return new PageData<>(items, page, pageSize,
                total == null ? 0 : total);
    }

    @Transactional(readOnly = true)
    public BagLabelBatchView detail(UUID batchUid) {
        requirePlatform();
        requireUuidV4(batchUid);
        return detailInternal(batchUid).orElseThrow(
                PlatformBagLabelApplicationService::notFound);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public BagLabelBatchView create(
            UUID operationUid,
            CreateBagLabelBatchRequest request) {
        requireUuidV4(operationUid);
        if (request == null || request.quantity() == null
                || request.quantity() < 1 || request.quantity() > 100) {
            throw invalidQuantity();
        }
        TargetWebAuditRequestContext.describe(
                GENERATE_ACTION, "new-bag-label-batch");
        PlatformActor actor = requirePlatform();
        byte[] requestDigest = requestDigest(actor, request.quantity());
        String fingerprint = HexFormat.of().formatHex(requestDigest);

        Optional<SuccessfulAudit> prior = audit.findSuccessful(operationUid);
        if (prior.isPresent()) {
            return replay(prior.orElseThrow(), actor, fingerprint);
        }

        LocalDateTime now = databaseNow();
        UUID batchUid = UUID.randomUUID();
        Set<String> unique = new LinkedHashSet<>();
        List<IssuedBagCode> issued = new ArrayList<>(request.quantity());
        while (issued.size() < request.quantity()) {
            IssuedBagCode candidate = bagCodes.issue();
            if (unique.add(candidate.value())) {
                issued.add(candidate);
            }
        }

        long batchId = insertAndReturnKey(connection -> {
            PreparedStatement statement = connection.prepareStatement("""
                    INSERT INTO rec_bag_label_batch (
                        batch_uid, operation_uid, key_id, label_count,
                        request_sha256, created_by_platform_admin_id,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, Statement.RETURN_GENERATED_KEYS);
            statement.setString(1, batchUid.toString());
            statement.setString(2, operationUid.toString());
            statement.setString(3, bagCodes.activeKeyId());
            statement.setInt(4, request.quantity());
            statement.setBytes(5, requestDigest);
            statement.setLong(6, actor.principalId());
            statement.setObject(7, now);
            return statement;
        });
        for (int index = 0; index < issued.size(); index++) {
            jdbc.update("""
                            INSERT INTO rec_bag_label_item (
                                batch_id, sequence_no, bag_code, created_at
                            ) VALUES (?, ?, ?, ?)
                            """,
                    batchId,
                    index + 1,
                    issued.get(index).value(),
                    now);
        }

        BagLabelBatchView result = detailInternal(batchUid)
                .orElseThrow(() -> new IllegalStateException(
                        "created bag-label batch is not readable"));
        audit.append(new AuditEntry(
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
                GENERATE_ACTION,
                TARGET_TYPE,
                batchUid.toString(),
                "WEB",
                "SUCCEEDED",
                actor.sessionUid(),
                null,
                writeJson(Map.of(
                        "fingerprint", fingerprint,
                        "batchUid", batchUid.toString())),
                Instant.now()));
        return result;
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public void delete(UUID batchUid) {
        requireUuidV4(batchUid);
        TargetWebAuditRequestContext.describe(
                DELETE_ACTION, batchUid.toString());
        PlatformActor actor = requirePlatform();
        BagLabelBatchView existing = detailInternal(batchUid)
                .orElseThrow(PlatformBagLabelApplicationService::notFound);
        int affected = jdbc.update(
                "DELETE FROM rec_bag_label_batch WHERE batch_uid = ?",
                batchUid.toString());
        if (affected != 1) {
            throw new IllegalStateException(
                    "delete bag-label batch affected " + affected + " rows");
        }
        audit.append(new AuditEntry(
                UUID.randomUUID(),
                UUID.randomUUID(),
                null,
                AuditScopeKind.PLATFORM,
                null,
                null,
                AuditActorKind.PLATFORM_ADMIN,
                actor.principalId(),
                null,
                null,
                null,
                actor.displayName(),
                DELETE_ACTION,
                TARGET_TYPE,
                batchUid.toString(),
                "WEB",
                "SUCCEEDED",
                actor.sessionUid(),
                null,
                writeJson(Map.of(
                        "keyId", existing.keyId(),
                        "quantity", existing.quantity())),
                Instant.now()));
    }

    private Optional<BagLabelBatchView> detailInternal(UUID batchUid) {
        Optional<BatchRow> batch = jdbc.query("""
                        SELECT batch.id, batch.batch_uid, batch.key_id,
                               batch.label_count, batch.created_at,
                               admin.platform_admin_uid,
                               admin.display_name
                        FROM rec_bag_label_batch batch
                        JOIN iam_platform_admin admin
                          ON admin.id = batch.created_by_platform_admin_id
                        WHERE batch.batch_uid = ?
                        """,
                (rs, ignored) -> new BatchRow(
                        rs.getLong("id"),
                        UUID.fromString(rs.getString("batch_uid")),
                        rs.getString("key_id"),
                        rs.getInt("label_count"),
                        UUID.fromString(
                                rs.getString("platform_admin_uid")),
                        rs.getString("display_name"),
                        rs.getObject("created_at", LocalDateTime.class)),
                batchUid.toString()).stream().findFirst();
        if (batch.isEmpty()) {
            return Optional.empty();
        }
        BatchRow row = batch.orElseThrow();
        List<BagLabelItemView> labels = jdbc.query("""
                        SELECT sequence_no, bag_code
                        FROM rec_bag_label_item
                        WHERE batch_id = ?
                        ORDER BY sequence_no
                        """,
                (rs, ignored) -> {
                    String bagCode = rs.getString("bag_code");
                    return new BagLabelItemView(
                            rs.getInt("sequence_no"),
                            bagCode,
                            bagCode);
                },
                row.id());
        if (labels.size() != row.quantity()) {
            throw new IllegalStateException(
                    "bag-label batch item count does not match header");
        }
        return Optional.of(new BagLabelBatchView(
                row.batchUid(),
                row.keyId(),
                row.quantity(),
                new PlatformAdminSummary(
                        row.platformAdminUid(), row.displayName()),
                instant(row.createdAt()),
                labels));
    }

    private BagLabelBatchView replay(
            SuccessfulAudit previous,
            PlatformActor actor,
            String fingerprint) {
        boolean sameOperation = previous.actorKind()
                == AuditActorKind.PLATFORM_ADMIN
                && previous.scopeKind() == AuditScopeKind.PLATFORM
                && previous.platformAdminId() != null
                && previous.platformAdminId() == actor.principalId()
                && GENERATE_ACTION.equals(previous.actionCode())
                && TARGET_TYPE.equals(previous.targetType());
        if (!sameOperation) {
            throw idempotencyConflict();
        }
        JsonNode summary = readJson(previous.safeChangeSummaryJson());
        if (!fingerprint.equals(summary.path("fingerprint").asText())) {
            throw idempotencyConflict();
        }
        UUID batchUid;
        try {
            batchUid = UUID.fromString(summary.path("batchUid").asText());
        } catch (IllegalArgumentException exception) {
            throw new IllegalStateException(
                    "bag-label audit is missing its batch identity",
                    exception);
        }
        return detailInternal(batchUid).orElseThrow(
                PlatformBagLabelApplicationService::deletedReplay);
    }

    private static BagLabelBatchSummary summary(
            String batchUid,
            String keyId,
            int quantity,
            String platformAdminUid,
            String displayName,
            LocalDateTime createdAt) {
        return new BagLabelBatchSummary(
                UUID.fromString(batchUid),
                keyId,
                quantity,
                new PlatformAdminSummary(
                        UUID.fromString(platformAdminUid), displayName),
                instant(createdAt));
    }

    private static byte[] requestDigest(
            PlatformActor actor,
            int quantity) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            digest.update(actor.principalUid().toString()
                    .getBytes(StandardCharsets.US_ASCII));
            digest.update((byte) 0);
            digest.update(GENERATE_ACTION
                    .getBytes(StandardCharsets.US_ASCII));
            digest.update((byte) 0);
            digest.update(Integer.toString(quantity)
                    .getBytes(StandardCharsets.US_ASCII));
            return digest.digest();
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "SHA-256 is unavailable", exception);
        }
    }

    private PlatformActor requirePlatform() {
        AuthorizedManagementScope authorized = managementScopes.authorize(
                new ManagementScopeAuthorizationQuery(
                        ManagementScopeAuthorizationQuery.Channel.WEB,
                        true,
                        null,
                        null,
                        "platform-admin.manage"));
        if (!authorized.platformActor()) {
            throw new TargetApiException(
                    403,
                    "AUTH.FORBIDDEN",
                    "当前账号不能管理平台袋码");
        }
        return authorized.persistenceRef().withScopeOnce(
                (tenantKey, organizationKeys, platformAdminKey,
                        staffAccountKey) -> {
                    if (platformAdminKey == null
                            || staffAccountKey != null
                            || tenantKey != null
                            || !organizationKeys.isEmpty()) {
                        throw new IllegalStateException(
                                "platform bag-label scope is inconsistent");
                    }
                    return new PlatformActor(
                            platformAdminKey,
                            authorized.principalUid(),
                            authorized.sessionUid(),
                            authorized.actorDisplayName());
                });
    }

    private LocalDateTime databaseNow() {
        LocalDateTime now = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        if (now == null) {
            throw new IllegalStateException("database time is unavailable");
        }
        return now;
    }

    private long insertAndReturnKey(
            PreparedStatementCreator creator) {
        KeyHolder holder = new GeneratedKeyHolder();
        int affected = jdbc.update(creator, holder);
        Number key = holder.getKey();
        if (affected != 1 || key == null) {
            throw new IllegalStateException(
                    "insert bag-label batch did not return a key");
        }
        return key.longValue();
    }

    private String writeJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "cannot serialize bag-label audit", exception);
        }
    }

    private JsonNode readJson(String value) {
        try {
            return objectMapper.readTree(value);
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "cannot read bag-label audit", exception);
        }
    }

    private static void requireUuidV4(UUID value) {
        if (value == null || value.version() != 4 || value.variant() != 2) {
            throw new TargetApiException(
                    400,
                    "VALIDATION.INVALID_UUID",
                    "请求标识必须是 UUIDv4");
        }
    }

    private static Instant instant(LocalDateTime value) {
        return value.toInstant(ZoneOffset.UTC);
    }

    private static TargetApiException invalidQuantity() {
        return new TargetApiException(
                400,
                "BAG_LABEL.QUANTITY_INVALID",
                "每批只能生成 1 到 100 个袋码");
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404,
                "RESOURCE.NOT_FOUND",
                "袋码批次不存在");
    }

    private static TargetApiException deletedReplay() {
        return new TargetApiException(
                410,
                "BAG_LABEL.BATCH_DELETED",
                "该幂等请求原先生成的袋码批次已经删除");
    }

    private static TargetApiException idempotencyConflict() {
        return new TargetApiException(
                409,
                "IDEMPOTENCY.KEY_REUSED",
                "该幂等键已经用于不同的袋码生成请求");
    }

    private record BatchRow(
            long id,
            UUID batchUid,
            String keyId,
            int quantity,
            UUID platformAdminUid,
            String displayName,
            LocalDateTime createdAt) {
    }

    private record PlatformActor(
            long principalId,
            UUID principalUid,
            UUID sessionUid,
            String displayName) {
    }
}
