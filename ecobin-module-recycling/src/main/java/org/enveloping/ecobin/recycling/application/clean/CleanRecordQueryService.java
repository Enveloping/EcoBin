package org.enveloping.ecobin.recycling.application.clean;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.enveloping.ecobin.identity.api.persistence.DeliveryOrderIdentityBatchRef.ReviewerKind;
import org.enveloping.ecobin.identity.api.port.DeliveryOrderIdentityQueryPort;
import org.enveloping.ecobin.identity.api.port.DeliveryScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.port.StartCleanIdentityParticipationPort;
import org.enveloping.ecobin.identity.api.query.DeliveryOrganizationUserFilterQuery;
import org.enveloping.ecobin.identity.api.query.DeliveryScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedDeliveryScope;
import org.enveloping.ecobin.identity.api.result.DeliveryOrderIdentityFacts;
import org.enveloping.ecobin.identity.api.result.LockedCleanOrganizationUser;
import org.enveloping.ecobin.identity.api.result.LockedMiniappCleanScope;
import org.enveloping.ecobin.identity.api.value.DeliveryIdentityFactToken;
import org.enveloping.ecobin.recycling.web.v1.CleanRecordModels.ChangeActor;
import org.enveloping.ecobin.recycling.web.v1.CleanRecordModels.CleanBagFacts;
import org.enveloping.ecobin.recycling.web.v1.CleanRecordModels.CleanBaselineSummary;
import org.enveloping.ecobin.recycling.web.v1.CleanRecordModels.CleanDetectionSummary;
import org.enveloping.ecobin.recycling.web.v1.CleanRecordModels.CleanEffectiveValue;
import org.enveloping.ecobin.recycling.web.v1.CleanRecordModels.CleanPhoto;
import org.enveloping.ecobin.recycling.web.v1.CleanRecordModels.CleanRecordChange;
import org.enveloping.ecobin.recycling.web.v1.CleanRecordModels.CleanRecordItem;
import org.enveloping.ecobin.recycling.web.v1.CleanRecordModels.CleanRecordSource;
import org.enveloping.ecobin.recycling.web.v1.CleanRecordModels.CleanWeightFacts;
import org.enveloping.ecobin.recycling.web.v1.CleanRecordModels.MiniappCleanAnomaly;
import org.enveloping.ecobin.recycling.web.v1.CleanRecordModels.MiniappCleanRecordDetail;
import org.enveloping.ecobin.recycling.web.v1.CleanRecordModels.WebCleanAnomaly;
import org.enveloping.ecobin.recycling.web.v1.CleanRecordModels.WebCleanRecordDetail;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.CursorPage;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

/** 清运员本人和 Web 后台的清运记录查询。 */
@Service
public class CleanRecordQueryService {

    private static final int DEFAULT_LIMIT = 20;
    private static final int MAX_LIMIT = 100;
    private static final List<String> PHOTO_POSITIONS = List.of(
            "FIRST_OPEN_INNER",
            "FIRST_OPEN_OUTER",
            "FINAL_CLOSE_INNER",
            "FINAL_CLOSE_OUTER");

    private final JdbcTemplate jdbc;
    private final NamedParameterJdbcTemplate namedJdbc;
    private final StartCleanIdentityParticipationPort miniappIdentity;
    private final DeliveryScopeAuthorizationPort authorization;
    private final DeliveryOrderIdentityQueryPort identityFacts;
    private final CleanRecordCursorCodec cursorCodec;
    private final ObjectMapper objectMapper;

    public CleanRecordQueryService(
            JdbcTemplate jdbc,
            NamedParameterJdbcTemplate namedJdbc,
            StartCleanIdentityParticipationPort miniappIdentity,
            DeliveryScopeAuthorizationPort authorization,
            DeliveryOrderIdentityQueryPort identityFacts,
            CleanRecordCursorCodec cursorCodec,
            ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.namedJdbc = namedJdbc;
        this.miniappIdentity = miniappIdentity;
        this.authorization = authorization;
        this.identityFacts = identityFacts;
        this.cursorCodec = cursorCodec;
        this.objectMapper = objectMapper;
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public CursorPage<CleanRecordItem> miniappRecords(
            String cursor,
            Integer requestedLimit) {
        int limit = limit(requestedLimit);
        LockedMiniappCleanScope lockedScope =
                miniappIdentity.lockCurrentMiniappScope();
        ScopeIds scope = lockedScope.organizationScopeRef()
                .withOrganizationScopeOnce(ScopeIds::new);
        LockedCleanOrganizationUser cleaner =
                miniappIdentity.lockCurrentCleaner(lockedScope);
        UUID cleanerUid = cleaner.organizationUserUid().value();
        String fingerprint = fingerprint(Map.of(
                "channel", "MINIAPP",
                "tenantCode", lockedScope.tenantCode(),
                "organizationCode", lockedScope.organizationCode(),
                "cleanerUserUid", cleanerUid.toString(),
                "limit", limit));
        PageAnchor anchor = anchor(cursor, fingerprint);
        return cleaner.organizationUserRef().withOrganizationUserOnce(
                (tenantId,
                 organizationId,
                 organizationUserId) -> {
                    requireScope(scope, tenantId, organizationId);
                    PageRows rows = records(
                            tenantId,
                            organizationId,
                            organizationUserId,
                            anchor,
                            fingerprint,
                            limit,
                            Filters.empty());
                    Map<Long, List<String>> anomalies =
                            anomalyCodes(rows.items());
                    List<CleanRecordItem> items = rows.items().stream()
                            .map(row -> item(
                                    row,
                                    cleanerUid,
                                    anomalies.getOrDefault(
                                            row.id(),
                                            List.of())))
                            .toList();
                    return new CursorPage<>(
                            items,
                            databaseNow(),
                            rows.nextCursor());
                });
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public MiniappCleanRecordDetail miniappRecord(
            String cleanRecordNo) {
        String recordNo = recordNo(cleanRecordNo);
        LockedMiniappCleanScope lockedScope =
                miniappIdentity.lockCurrentMiniappScope();
        ScopeIds scope = lockedScope.organizationScopeRef()
                .withOrganizationScopeOnce(ScopeIds::new);
        LockedCleanOrganizationUser cleaner =
                miniappIdentity.lockCurrentCleaner(lockedScope);
        UUID cleanerUid = cleaner.organizationUserUid().value();
        return cleaner.organizationUserRef().withOrganizationUserOnce(
                (tenantId,
                 organizationId,
                 organizationUserId) -> {
                    requireScope(scope, tenantId, organizationId);
                    DetailBundle detail = detail(
                            tenantId,
                            organizationId,
                            organizationUserId,
                            recordNo);
                    return miniappDetail(detail, cleanerUid);
                });
    }

    @Transactional(readOnly = true)
    public CursorPage<CleanRecordItem> webRecords(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String cursor,
            Integer requestedLimit,
            String resultKind,
            UUID cleanerUserUid,
            String deviceCode,
            Integer portNo,
            String removedBagQr,
            String installedBagQr,
            String anomalyCode,
            String requestedPhotoCompleteness,
            Instant occurredFrom,
            Instant occurredTo) {
        AuthorizedDeliveryScope authorized = authorize(
                platformPath,
                tenantCode,
                organizationCode,
                true,
                false);
        int limit = limit(requestedLimit);
        Filters filters = filters(
                resultKind,
                deviceCode,
                portNo,
                removedBagQr,
                installedBagQr,
                anomalyCode,
                requestedPhotoCompleteness,
                occurredFrom,
                occurredTo);
        String fingerprint = fingerprint(filters.fingerprintFields(
                authorized,
                cleanerUserUid,
                limit));
        PageAnchor anchor = anchor(cursor, fingerprint);

        Optional<ResolvedUser> user = Optional.empty();
        if (cleanerUserUid != null) {
            var reference = identityFacts.resolveOrganizationUserFilter(
                    new DeliveryOrganizationUserFilterQuery(
                            authorized.tenantCode(),
                            authorized.organizationCode(),
                            new OrganizationUserUid(cleanerUserUid)));
            if (reference.isEmpty()) {
                authorized.persistenceRef().withScopeOnce(
                        (ignoredTenant,
                         ignoredOrganization,
                         ignoredPlatform,
                         ignoredStaff) -> null);
                return new CursorPage<>(
                        List.of(), databaseNow(), null);
            }
            user = Optional.of(reference.orElseThrow()
                    .withOrganizationUserOnce(ResolvedUser::new));
        }
        ScopeIds scope = authorized.persistenceRef().withScopeOnce(
                (resolvedTenant,
                 resolvedOrganization,
                 ignoredPlatform,
                 ignoredStaff) -> new ScopeIds(
                        resolvedTenant,
                        resolvedOrganization));
        Long cleanerId = null;
        if (user.isPresent()) {
            ResolvedUser resolved = user.orElseThrow();
            requireScope(
                    scope,
                    resolved.tenantId(),
                    resolved.organizationId());
            cleanerId = resolved.organizationUserId();
        }
        PageRows rows = records(
                scope.tenantId(),
                scope.organizationId(),
                cleanerId,
                anchor,
                fingerprint,
                limit,
                filters);
        Map<Long, List<String>> anomalies = anomalyCodes(rows.items());
        Map<Long, UUID> cleaners = cleaners(scope, rows.items());
        List<CleanRecordItem> items = rows.items().stream()
                .map(row -> item(
                        row,
                        required(cleaners, row.cleanerId()),
                        anomalies.getOrDefault(row.id(), List.of())))
                .toList();
        return new CursorPage<>(
                items,
                databaseNow(),
                rows.nextCursor());
    }

    @Transactional(readOnly = true)
    public WebCleanRecordDetail webRecord(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String cleanRecordNo) {
        AuthorizedDeliveryScope authorized = authorize(
                platformPath,
                tenantCode,
                organizationCode,
                true,
                false);
        String recordNo = recordNo(cleanRecordNo);
        ScopeIds scope = authorized.persistenceRef().withScopeOnce(
                (resolvedTenant,
                 resolvedOrganization,
                 ignoredPlatform,
                 ignoredStaff) -> new ScopeIds(
                        resolvedTenant,
                        resolvedOrganization));
        DetailBundle detail = detail(
                scope.tenantId(),
                scope.organizationId(),
                null,
                recordNo);
        IdentityBundle identities = identities(
                scope,
                detail.root(),
                detail.latestChange() == null
                        ? List.of()
                        : List.of(detail.latestChange()));
        return webDetail(
                detail,
                identities.cleanerUid(),
                detail.latestChange() == null
                        ? null
                        : change(
                                detail.latestChange(),
                                identities.actors().get(
                                        detail.latestChange().id())));
    }

    @Transactional(readOnly = true)
    public CursorPage<CleanRecordChange> changes(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String cleanRecordNo,
            String cursor,
            Integer requestedLimit) {
        AuthorizedDeliveryScope authorized = authorize(
                platformPath,
                tenantCode,
                organizationCode,
                true,
                false);
        String recordNo = recordNo(cleanRecordNo);
        int limit = limit(requestedLimit);
        String fingerprint = fingerprint(Map.of(
                "channel", "WEB_CHANGE",
                "principalUid", authorized.principalUid().toString(),
                "tenantCode", authorized.tenantCode(),
                "organizationCode", authorized.organizationCode(),
                "cleanRecordNo", recordNo,
                "limit", limit));
        PageAnchor anchor = anchor(cursor, fingerprint);
        ScopeIds scope = authorized.persistenceRef().withScopeOnce(
                (resolvedTenant,
                 resolvedOrganization,
                 ignoredPlatform,
                 ignoredStaff) -> new ScopeIds(
                        resolvedTenant,
                        resolvedOrganization));
        RecordVersion record = jdbc.query("""
                        SELECT id, lock_version
                        FROM rec_clean_record
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND clean_record_no = ?
                        """,
                (rs, ignored) -> new RecordVersion(
                        rs.getLong("id"),
                        rs.getLong("lock_version")),
                scope.tenantId(),
                scope.organizationId(),
                recordNo).stream().findFirst().orElseThrow(
                CleanRecordQueryService::notFound);
        long highWatermark = anchor == null
                ? record.version()
                : anchor.highWatermark();
        long lastVersion = anchor == null
                ? Long.MAX_VALUE
                : parseVersion(anchor.lastStableKey());
        List<ChangeRow> fetched = jdbc.query("""
                        SELECT id, change_uid, from_version, to_version,
                               before_effective_removed_net_weight_g,
                               before_effective_weight_source,
                               before_record_remark,
                               after_effective_removed_net_weight_g,
                               after_effective_weight_source,
                               after_record_remark,
                               reason, actor_kind,
                               platform_admin_id, staff_account_id,
                               changed_at
                        FROM rec_clean_record_change
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND clean_record_id = ?
                          AND to_version <= ?
                          AND to_version < ?
                        ORDER BY to_version DESC
                        LIMIT ?
                        """,
                (rs, ignored) -> changeRow(rs),
                scope.tenantId(),
                scope.organizationId(),
                record.id(),
                highWatermark,
                lastVersion,
                limit + 1);
        boolean hasMore = fetched.size() > limit;
        List<ChangeRow> rows = hasMore
                ? List.copyOf(fetched.subList(0, limit))
                : List.copyOf(fetched);
        Map<Long, DeliveryOrderIdentityFacts.ReviewerIdentity> actors =
                actors(scope, rows);
        List<CleanRecordChange> items = rows.stream()
                .map(row -> change(row, actors.get(row.id())))
                .toList();
        String nextCursor = null;
        if (hasMore && !rows.isEmpty()) {
            ChangeRow last = rows.getLast();
            nextCursor = cursorCodec.encode(
                    highWatermark,
                    last.changedAt(),
                    Long.toString(last.toVersion()),
                    fingerprint);
        }
        return new CursorPage<>(items, databaseNow(), nextCursor);
    }

    private PageRows records(
            long tenantId,
            long organizationId,
            Long cleanerId,
            PageAnchor anchor,
            String fingerprint,
            int limit,
            Filters filters) {
        long highWatermark = anchor == null
                ? highWatermark(tenantId, organizationId)
                : anchor.highWatermark();
        StringBuilder sql = new StringBuilder("""
                SELECT record.id, record.clean_record_no,
                       record.clean_operation_id,
                       operation.operation_uid,
                       record.cleaner_organization_user_id,
                       asset.device_public_code,
                       port.port_no,
                       record.old_bag_code_snapshot,
                       record.new_bag_code_snapshot,
                       record.device_occurred_at,
                       record.completed_at,
                       COALESCE(record.device_occurred_at,
                                record.completed_at) AS sort_at,
                       record.recalculated_removed_net_weight_status,
                       record.recalculated_removed_net_weight_g,
                       record.effective_removed_net_weight_g,
                       record.effective_weight_source,
                       record.record_class,
                       record.record_remark,
                       record.lock_version,
                       CASE WHEN (
                           SELECT COUNT(*)
                           FROM rec_clean_photo photo
                           WHERE photo.clean_operation_id =
                                 record.clean_operation_id
                             AND photo.status = 'AVAILABLE'
                       ) = 4 THEN 'COMPLETE' ELSE 'INCOMPLETE' END
                           AS photo_completeness
                FROM rec_clean_record record
                JOIN rec_clean_operation operation
                  ON operation.id = record.clean_operation_id
                JOIN dev_device_asset asset
                  ON asset.id = record.asset_id
                JOIN dev_port port ON port.id = record.port_id
                WHERE record.tenant_id = :tenantId
                  AND record.organization_id = :organizationId
                  AND record.visibility_sequence_no <= :highWatermark
                """);
        MapSqlParameterSource parameters = new MapSqlParameterSource()
                .addValue("tenantId", tenantId)
                .addValue("organizationId", organizationId)
                .addValue("highWatermark", highWatermark)
                .addValue("limit", limit + 1);
        if (cleanerId != null) {
            sql.append(" AND record.cleaner_organization_user_id = :cleanerId");
            parameters.addValue("cleanerId", cleanerId);
        }
        filters.append(sql, parameters);
        if (anchor != null) {
            sql.append("""
                     AND (
                         COALESCE(record.device_occurred_at,
                                  record.completed_at) < :lastSortAt
                         OR (
                             COALESCE(record.device_occurred_at,
                                      record.completed_at) = :lastSortAt
                             AND record.clean_record_no < :lastRecordNo
                         )
                     )
                    """);
            parameters
                    .addValue("lastSortAt", anchor.lastSortTime())
                    .addValue("lastRecordNo", anchor.lastStableKey());
        }
        sql.append("""
                 ORDER BY sort_at DESC, record.clean_record_no DESC
                 LIMIT :limit
                """);
        List<SummaryRow> fetched = namedJdbc.query(
                sql.toString(),
                parameters,
                (rs, ignored) -> new SummaryRow(
                        rs.getLong("id"),
                        rs.getString("clean_record_no"),
                        rs.getLong("clean_operation_id"),
                        UUID.fromString(rs.getString("operation_uid")),
                        rs.getLong("cleaner_organization_user_id"),
                        rs.getString("device_public_code"),
                        rs.getInt("port_no"),
                        rs.getString("old_bag_code_snapshot"),
                        rs.getString("new_bag_code_snapshot"),
                        nullableTime(rs.getObject(
                                "device_occurred_at",
                                LocalDateTime.class)),
                        rs.getObject("completed_at", LocalDateTime.class),
                        rs.getObject("sort_at", LocalDateTime.class),
                        rs.getString(
                                "recalculated_removed_net_weight_status"),
                        nullableLong(
                                rs.getObject(
                                        "recalculated_removed_net_weight_g")),
                        nullableLong(rs.getObject(
                                "effective_removed_net_weight_g")),
                        rs.getString("effective_weight_source"),
                        rs.getString("record_class"),
                        rs.getString("record_remark"),
                        rs.getLong("lock_version"),
                        rs.getString("photo_completeness")));
        boolean hasMore = fetched.size() > limit;
        List<SummaryRow> rows = hasMore
                ? List.copyOf(fetched.subList(0, limit))
                : List.copyOf(fetched);
        String nextCursor = null;
        if (hasMore && !rows.isEmpty()) {
            SummaryRow last = rows.getLast();
            nextCursor = cursorCodec.encode(
                    highWatermark,
                    last.sortAt(),
                    last.recordNo(),
                    fingerprint);
        }
        return new PageRows(rows, nextCursor);
    }

    private DetailBundle detail(
            long tenantId,
            long organizationId,
            Long cleanerId,
            String recordNo) {
        String cleanerClause = cleanerId == null
                ? ""
                : " AND record.cleaner_organization_user_id = ?";
        List<Object> arguments = new ArrayList<>(List.of(
                tenantId, organizationId, recordNo));
        if (cleanerId != null) {
            arguments.add(cleanerId);
        }
        RootRow root = jdbc.query("""
                        SELECT record.*,
                               operation.operation_uid,
                               asset.device_public_code,
                               port.port_no,
                               event.event_uid,
                               command.command_uid,
                               result.clean_new_baseline_weight_g
                        FROM rec_clean_record record
                        JOIN rec_clean_operation operation
                          ON operation.id = record.clean_operation_id
                        JOIN dev_device_asset asset
                          ON asset.id = record.asset_id
                        JOIN dev_port port ON port.id = record.port_id
                        JOIN dev_physical_result result
                          ON result.id = record.physical_result_id
                        JOIN dev_edge_event event
                          ON event.id = result.edge_event_id
                        JOIN dev_device_command command
                          ON command.id = result.command_id
                        WHERE record.tenant_id = ?
                          AND record.organization_id = ?
                          AND record.clean_record_no = ?
                        """ + cleanerClause,
                (rs, ignored) -> new RootRow(
                        rs.getLong("id"),
                        rs.getString("clean_record_no"),
                        rs.getLong("clean_operation_id"),
                        UUID.fromString(rs.getString("operation_uid")),
                        rs.getLong("cleaner_organization_user_id"),
                        rs.getString("device_public_code"),
                        rs.getInt("port_no"),
                        UUID.fromString(rs.getString("event_uid")),
                        UUID.fromString(rs.getString("command_uid")),
                        rs.getLong("clean_config_version_no"),
                        rs.getString("old_bag_binding_state"),
                        rs.getString("old_baseline_state"),
                        rs.getString("old_bag_code_snapshot"),
                        rs.getString("new_bag_code_snapshot"),
                        rs.getString("pre_unlock_weight_status"),
                        nullableLong(rs.getObject("pre_unlock_weight_g")),
                        nullableLong(rs.getObject("old_baseline_weight_g")),
                        rs.getString(
                                "device_removed_net_weight_status"),
                        nullableLong(rs.getObject(
                                "device_removed_net_weight_g")),
                        rs.getString(
                                "recalculated_removed_net_weight_status"),
                        nullableLong(rs.getObject(
                                "recalculated_removed_net_weight_g")),
                        rs.getString("final_total_weight_status"),
                        nullableLong(rs.getObject("final_total_weight_g")),
                        nullableLong(rs.getObject(
                                "clean_new_baseline_weight_g")),
                        nullableLong(rs.getObject(
                                "effective_removed_net_weight_g")),
                        rs.getString("effective_weight_source"),
                        rs.getString("record_remark"),
                        rs.getLong("lock_version"),
                        rs.getString("record_class"),
                        nullableTime(rs.getObject(
                                "device_occurred_at",
                                LocalDateTime.class)),
                        rs.getObject(
                                "backend_received_at",
                                LocalDateTime.class),
                        rs.getObject("completed_at", LocalDateTime.class)),
                arguments.toArray()).stream().findFirst().orElseThrow(
                CleanRecordQueryService::notFound);
        List<AnomalyRow> anomalies = jdbc.query("""
                        SELECT anomaly_code, diagnostic_json, detected_at
                        FROM rec_clean_anomaly
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND clean_record_id = ?
                        ORDER BY anomaly_code
                        """,
                (rs, ignored) -> new AnomalyRow(
                        rs.getString("anomaly_code"),
                        rs.getString("diagnostic_json"),
                        rs.getObject("detected_at", LocalDateTime.class)),
                tenantId,
                organizationId,
                root.id());
        List<PhotoRow> photos = jdbc.query("""
                        SELECT position, status, object_url,
                               captured_at, missing_reason
                        FROM rec_clean_photo
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND clean_operation_id = ?
                        """,
                (rs, ignored) -> new PhotoRow(
                        rs.getString("position"),
                        rs.getString("status"),
                        rs.getString("object_url"),
                        nullableTime(rs.getObject(
                                "captured_at",
                                LocalDateTime.class)),
                        rs.getString("missing_reason")),
                tenantId,
                organizationId,
                root.operationId());
        photos = orderedPhotos(photos);
        BaselineRow baseline = jdbc.query("""
                        SELECT version_no, baseline_weight_g,
                               established_at
                        FROM rec_port_weight_baseline
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND source_clean_record_id = ?
                        """,
                (rs, ignored) -> new BaselineRow(
                        rs.getLong("version_no"),
                        rs.getLong("baseline_weight_g"),
                        rs.getObject(
                                "established_at",
                                LocalDateTime.class)),
                tenantId,
                organizationId,
                root.id()).stream().findFirst().orElse(null);
        DetectionRow detection = jdbc.query("""
                        SELECT detection_uid, status, final_result,
                               failure_code, completed_at
                        FROM rec_fullness_detection
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND clean_record_id = ?
                        """,
                (rs, ignored) -> new DetectionRow(
                        UUID.fromString(rs.getString("detection_uid")),
                        rs.getString("status"),
                        rs.getString("final_result"),
                        rs.getString("failure_code"),
                        nullableTime(rs.getObject(
                                "completed_at",
                                LocalDateTime.class))),
                tenantId,
                organizationId,
                root.id()).stream().findFirst().orElse(null);
        ChangeRow latest = jdbc.query("""
                        SELECT id, change_uid, from_version, to_version,
                               before_effective_removed_net_weight_g,
                               before_effective_weight_source,
                               before_record_remark,
                               after_effective_removed_net_weight_g,
                               after_effective_weight_source,
                               after_record_remark,
                               reason, actor_kind,
                               platform_admin_id, staff_account_id,
                               changed_at
                        FROM rec_clean_record_change
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND clean_record_id = ?
                        ORDER BY to_version DESC
                        LIMIT 1
                        """,
                (rs, ignored) -> changeRow(rs),
                tenantId,
                organizationId,
                root.id()).stream().findFirst().orElse(null);
        return new DetailBundle(
                root, anomalies, photos, baseline, detection, latest);
    }

    private MiniappCleanRecordDetail miniappDetail(
            DetailBundle detail,
            UUID cleanerUid) {
        RootRow root = detail.root();
        return new MiniappCleanRecordDetail(
                root.recordNo(),
                source(root, cleanerUid),
                bags(root),
                weights(root),
                baseline(root, detail.baseline()),
                detection(detail.detection()),
                root.recordClass(),
                detail.anomalies().stream()
                        .map(row -> new MiniappCleanAnomaly(
                                row.code(),
                                instant(row.detectedAt())))
                        .toList(),
                detail.photos().stream()
                        .map(CleanRecordQueryService::photo)
                        .toList(),
                effective(root));
    }

    private WebCleanRecordDetail webDetail(
            DetailBundle detail,
            UUID cleanerUid,
            CleanRecordChange latestChange) {
        RootRow root = detail.root();
        return new WebCleanRecordDetail(
                root.recordNo(),
                source(root, cleanerUid),
                bags(root),
                weights(root),
                baseline(root, detail.baseline()),
                detection(detail.detection()),
                root.recordClass(),
                detail.anomalies().stream()
                        .map(row -> new WebCleanAnomaly(
                                row.code(),
                                instant(row.detectedAt()),
                                diagnostic(row.diagnosticJson())))
                        .toList(),
                detail.photos().stream()
                        .map(CleanRecordQueryService::photo)
                        .toList(),
                effective(root),
                latestChange);
    }

    private static CleanRecordItem item(
            SummaryRow row,
            UUID cleanerUid,
            List<String> anomalyCodes) {
        return new CleanRecordItem(
                row.recordNo(),
                row.operationUid(),
                cleanerUid,
                row.deviceCode(),
                row.portNo(),
                row.removedBag(),
                row.installedBag(),
                instant(row.deviceOccurredAt()),
                kg(row.recalculatedWeight()),
                kg(row.effectiveWeight()),
                row.effectiveSource(),
                row.recalculatedStatus(),
                row.recordClass(),
                anomalyCodes,
                row.photoCompleteness(),
                row.remark(),
                row.version());
    }

    private static CleanRecordSource source(
            RootRow row,
            UUID cleanerUid) {
        return new CleanRecordSource(
                row.operationUid(),
                row.eventUid(),
                row.commandUid(),
                row.deviceCode(),
                row.portNo(),
                cleanerUid,
                row.configVersion(),
                instant(row.deviceOccurredAt()),
                instant(row.backendReceivedAt()),
                instant(row.completedAt()));
    }

    private static CleanBagFacts bags(RootRow row) {
        return new CleanBagFacts(
                row.oldBagState(),
                row.oldBagCode(),
                row.newBagCode());
    }

    private static CleanWeightFacts weights(RootRow row) {
        return new CleanWeightFacts(
                row.preUnlockStatus(),
                kg(row.preUnlockWeight()),
                row.oldBaselineState(),
                kg(row.oldBaselineWeight()),
                row.deviceRemovedStatus(),
                kg(row.deviceRemovedWeight()),
                row.recalculatedStatus(),
                kg(row.recalculatedWeight()),
                row.finalStatus(),
                kg(row.finalWeight()),
                kg(row.candidateBaselineWeight()));
    }

    private static CleanBaselineSummary baseline(
            RootRow root,
            BaselineRow row) {
        return row == null
                ? new CleanBaselineSummary(
                        false, null, root.newBagCode(), null, null)
                : new CleanBaselineSummary(
                        true,
                        row.version(),
                        root.newBagCode(),
                        kg(row.weight()),
                        instant(row.establishedAt()));
    }

    private static CleanDetectionSummary detection(DetectionRow row) {
        return row == null
                ? null
                : new CleanDetectionSummary(
                        row.uid(),
                        row.status(),
                        row.finalResult(),
                        row.failureCode(),
                        instant(row.completedAt()));
    }

    private static CleanPhoto photo(PhotoRow row) {
        return new CleanPhoto(
                row.position(),
                row.status(),
                row.url(),
                instant(row.capturedAt()),
                row.missingReason());
    }

    private static CleanEffectiveValue effective(RootRow row) {
        return new CleanEffectiveValue(
                kg(row.effectiveWeight()),
                row.effectiveSource(),
                row.effectiveWeight() != null,
                row.remark(),
                row.version());
    }

    private static CleanRecordChange change(
            ChangeRow row,
            DeliveryOrderIdentityFacts.ReviewerIdentity actor) {
        if (actor == null) {
            throw new IllegalStateException(
                    "clean change actor identity is incomplete");
        }
        return new CleanRecordChange(
                row.uid(),
                row.fromVersion(),
                row.toVersion(),
                kg(row.beforeWeight()),
                row.beforeSource(),
                row.beforeRemark(),
                kg(row.afterWeight()),
                row.afterSource(),
                row.afterRemark(),
                row.reason(),
                new ChangeActor(
                        actor.actorKind().name(),
                        actor.actorUid().value(),
                        actor.displayName()),
                instant(row.changedAt()));
    }

    private Map<Long, List<String>> anomalyCodes(
            List<SummaryRow> rows) {
        if (rows.isEmpty()) {
            return Map.of();
        }
        List<Long> ids = rows.stream().map(SummaryRow::id).toList();
        Map<Long, List<String>> mutable = new HashMap<>();
        namedJdbc.queryForList("""
                        SELECT clean_record_id, anomaly_code
                        FROM rec_clean_anomaly
                        WHERE clean_record_id IN (:ids)
                        ORDER BY anomaly_code
                        """,
                Map.of("ids", ids)).forEach(row -> mutable
                .computeIfAbsent(
                        ((Number) row.get("clean_record_id"))
                                .longValue(),
                        ignored -> new ArrayList<>())
                .add((String) row.get("anomaly_code")));
        Map<Long, List<String>> result = new HashMap<>();
        mutable.forEach((id, codes) ->
                result.put(id, List.copyOf(codes)));
        return Map.copyOf(result);
    }

    private Map<Long, UUID> cleaners(
            ScopeIds scope,
            List<SummaryRow> rows) {
        if (rows.isEmpty()) {
            return Map.of();
        }
        Map<Long, DeliveryIdentityFactToken> tokens =
                new LinkedHashMap<>();
        rows.forEach(row -> tokens.computeIfAbsent(
                row.cleanerId(),
                ignored -> DeliveryIdentityFactToken.create()));
        List<CleanRecordIdentityBatchRef.UserEntry> entries =
                tokens.entrySet().stream()
                        .map(entry ->
                                new CleanRecordIdentityBatchRef.UserEntry(
                                        entry.getValue(),
                                        scope.tenantId(),
                                        scope.organizationId(),
                                        entry.getKey()))
                        .toList();
        DeliveryOrderIdentityFacts facts = identityFacts.resolveFacts(
                CleanRecordIdentityBatchRef.issue(entries, List.of()));
        Map<Long, UUID> result = new HashMap<>();
        tokens.forEach((id, token) -> {
            OrganizationUserUid uid = facts.organizationUsers().get(token);
            if (uid == null) {
                throw new IllegalStateException(
                        "cleaner identity is incomplete");
            }
            result.put(id, uid.value());
        });
        return Map.copyOf(result);
    }

    private IdentityBundle identities(
            ScopeIds scope,
            RootRow root,
            List<ChangeRow> changes) {
        DeliveryIdentityFactToken cleanerToken =
                DeliveryIdentityFactToken.create();
        List<CleanRecordIdentityBatchRef.ActorEntry> actorEntries =
                new ArrayList<>();
        Map<Long, DeliveryIdentityFactToken> actorTokens =
                new HashMap<>();
        for (ChangeRow row : changes) {
            DeliveryIdentityFactToken token =
                    DeliveryIdentityFactToken.create();
            actorTokens.put(row.id(), token);
            actorEntries.add(actorEntry(scope, row, token));
        }
        DeliveryOrderIdentityFacts facts = identityFacts.resolveFacts(
                CleanRecordIdentityBatchRef.issue(
                        List.of(new CleanRecordIdentityBatchRef.UserEntry(
                                cleanerToken,
                                scope.tenantId(),
                                scope.organizationId(),
                                root.cleanerId())),
                        actorEntries));
        OrganizationUserUid cleaner =
                facts.organizationUsers().get(cleanerToken);
        if (cleaner == null) {
            throw new IllegalStateException(
                    "cleaner identity is incomplete");
        }
        Map<Long, DeliveryOrderIdentityFacts.ReviewerIdentity> actors =
                new HashMap<>();
        actorTokens.forEach((id, token) -> {
            var actor = facts.reviewers().get(token);
            if (actor == null) {
                throw new IllegalStateException(
                        "clean change actor identity is incomplete");
            }
            actors.put(id, actor);
        });
        return new IdentityBundle(cleaner.value(), Map.copyOf(actors));
    }

    private Map<Long, DeliveryOrderIdentityFacts.ReviewerIdentity> actors(
            ScopeIds scope,
            List<ChangeRow> changes) {
        if (changes.isEmpty()) {
            return Map.of();
        }
        Map<Long, DeliveryIdentityFactToken> tokens = new HashMap<>();
        List<CleanRecordIdentityBatchRef.ActorEntry> entries =
                new ArrayList<>();
        for (ChangeRow row : changes) {
            DeliveryIdentityFactToken token =
                    DeliveryIdentityFactToken.create();
            tokens.put(row.id(), token);
            entries.add(actorEntry(scope, row, token));
        }
        DeliveryOrderIdentityFacts facts = identityFacts.resolveFacts(
                CleanRecordIdentityBatchRef.issue(List.of(), entries));
        Map<Long, DeliveryOrderIdentityFacts.ReviewerIdentity> result =
                new HashMap<>();
        tokens.forEach((id, token) -> {
            var actor = facts.reviewers().get(token);
            if (actor == null) {
                throw new IllegalStateException(
                        "clean change actor identity is incomplete");
            }
            result.put(id, actor);
        });
        return Map.copyOf(result);
    }

    private static CleanRecordIdentityBatchRef.ActorEntry actorEntry(
            ScopeIds scope,
            ChangeRow row,
            DeliveryIdentityFactToken token) {
        return new CleanRecordIdentityBatchRef.ActorEntry(
                token,
                scope.tenantId(),
                scope.organizationId(),
                ReviewerKind.valueOf(row.actorKind()),
                row.platformAdminId(),
                row.staffAccountId());
    }

    private AuthorizedDeliveryScope authorize(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            boolean requireRead,
            boolean requireEdit) {
        AuthorizedDeliveryScope result = authorization.authorize(
                new DeliveryScopeAuthorizationQuery(
                        platformPath,
                        tenantCode,
                        organizationCode));
        if ((requireRead && !result.cleanRead())
                || (requireEdit && !result.cleanEdit())) {
            throw new TargetApiException(
                    403,
                    "AUTH.CAPABILITY_REQUIRED",
                    "当前账号缺少清运记录所需能力");
        }
        return result;
    }

    private long highWatermark(long tenantId, long organizationId) {
        Long result = jdbc.queryForObject("""
                        SELECT last_visibility_sequence_no
                        FROM rec_organization_clean_record_counter
                        WHERE tenant_id = ?
                          AND organization_id = ?
                        """,
                Long.class,
                tenantId,
                organizationId);
        return result == null ? 0 : result;
    }

    private Instant databaseNow() {
        LocalDateTime now = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)",
                LocalDateTime.class);
        if (now == null) {
            throw new IllegalStateException(
                    "database time is unavailable");
        }
        return instant(now);
    }

    private Map<String, Object> diagnostic(String json) {
        if (json == null) {
            return Map.of();
        }
        try {
            @SuppressWarnings("unchecked")
            Map<String, Object> value = objectMapper.readValue(
                    json,
                    Map.class);
            return Map.copyOf(value);
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "clean anomaly diagnostic JSON is invalid",
                    exception);
        }
    }

    private String fingerprint(Map<String, ?> fields) {
        try {
            byte[] canonical = objectMapper.writeValueAsBytes(fields);
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256")
                            .digest(canonical));
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "clean record filter cannot be fingerprinted",
                    exception);
        }
    }

    private PageAnchor anchor(
            String cursor,
            String fingerprint) {
        if (cursor == null || cursor.isBlank()) {
            return null;
        }
        var decoded = cursorCodec.decode(cursor, fingerprint);
        return new PageAnchor(
                decoded.highWatermark(),
                decoded.lastSortTime(),
                decoded.lastStableKey());
    }

    private static Filters filters(
            String requestedResultKind,
            String deviceCode,
            Integer portNo,
            String removedBagQr,
            String installedBagQr,
            String anomalyCode,
            String requestedPhotoCompleteness,
            Instant occurredFrom,
            Instant occurredTo) {
        String resultKind = enumValue(
                requestedResultKind,
                "resultKind",
                List.of("NORMAL", "SYSTEM_ANOMALY"));
        String photoCompleteness = enumValue(
                requestedPhotoCompleteness,
                "photoCompleteness",
                List.of("COMPLETE", "INCOMPLETE"));
        String normalizedDeviceCode = sized(
                deviceCode, "deviceCode", 64);
        String removedBag = bag(removedBagQr, "removedBagQr");
        String installedBag = bag(
                installedBagQr, "installedBagQr");
        String anomaly = sized(anomalyCode, "anomalyCode", 64);
        if (portNo != null && (portNo < 1 || portNo > 6)) {
            throw validation(
                    "portNo", "portNo 必须在 1 到 6 之间");
        }
        if (occurredFrom != null
                && occurredTo != null
                && !occurredFrom.isBefore(occurredTo)) {
            throw validation(
                    "occurredTo",
                    "occurredTo 必须晚于 occurredFrom");
        }
        return new Filters(
                resultKind,
                normalizedDeviceCode,
                portNo,
                removedBag,
                installedBag,
                anomaly,
                photoCompleteness,
                time(occurredFrom),
                time(occurredTo));
    }

    private static int limit(Integer value) {
        if (value == null) {
            return DEFAULT_LIMIT;
        }
        if (value < 1 || value > MAX_LIMIT) {
            throw validation(
                    "limit", "limit 必须在 1 到 100 之间");
        }
        return value;
    }

    private static String recordNo(String value) {
        if (value == null
                || !value.trim().matches("[A-Za-z0-9_-]{8,64}")) {
            throw notFound();
        }
        return value.trim();
    }

    private static String bag(String value, String field) {
        String normalized = sized(value, field, 64);
        if (normalized != null
                && !normalized.matches("[A-Za-z0-9_-]{8,64}")) {
            throw validation(field, field + " 格式无效");
        }
        return normalized;
    }

    private static String sized(
            String value,
            String field,
            int maximum) {
        if (value == null || value.isBlank()) {
            return null;
        }
        String normalized = value.trim();
        if (normalized.length() > maximum) {
            throw validation(
                    field,
                    field + " 长度不能超过 " + maximum);
        }
        return normalized;
    }

    private static String enumValue(
            String value,
            String field,
            List<String> allowed) {
        if (value == null || value.isBlank()) {
            return null;
        }
        String normalized = value.trim().toUpperCase();
        if (!allowed.contains(normalized)) {
            throw validation(field, field + " 取值无效");
        }
        return normalized;
    }

    private static long parseVersion(String value) {
        try {
            long parsed = Long.parseLong(value);
            if (parsed < 1) {
                throw new NumberFormatException();
            }
            return parsed;
        } catch (NumberFormatException exception) {
            throw new TargetApiException(
                    400,
                    "COMMON.INVALID_CURSOR",
                    "清运记录分页游标无效或已过期");
        }
    }

    private static void requireScope(
            ScopeIds expected,
            long tenantId,
            long organizationId) {
        if (expected.tenantId() != tenantId
                || expected.organizationId() != organizationId) {
            throw new IllegalStateException(
                    "clean record scope changed after authorization");
        }
    }

    private static UUID required(Map<Long, UUID> values, long key) {
        UUID value = values.get(key);
        if (value == null) {
            throw new IllegalStateException(
                    "cleaner identity is incomplete");
        }
        return value;
    }

    private static List<PhotoRow> orderedPhotos(List<PhotoRow> rows) {
        if (rows.size() != PHOTO_POSITIONS.size()) {
            throw new IllegalStateException(
                    "clean record must have exactly four photo slots");
        }
        Map<String, PhotoRow> byPosition = new HashMap<>();
        rows.forEach(row -> byPosition.put(row.position(), row));
        return PHOTO_POSITIONS.stream()
                .map(position -> {
                    PhotoRow row = byPosition.get(position);
                    if (row == null) {
                        throw new IllegalStateException(
                                "clean record photo slots are incomplete");
                    }
                    return row;
                })
                .toList();
    }

    private static ChangeRow changeRow(java.sql.ResultSet rs)
            throws java.sql.SQLException {
        return new ChangeRow(
                rs.getLong("id"),
                UUID.fromString(rs.getString("change_uid")),
                rs.getLong("from_version"),
                rs.getLong("to_version"),
                nullableLong(rs.getObject(
                        "before_effective_removed_net_weight_g")),
                rs.getString("before_effective_weight_source"),
                rs.getString("before_record_remark"),
                nullableLong(rs.getObject(
                        "after_effective_removed_net_weight_g")),
                rs.getString("after_effective_weight_source"),
                rs.getString("after_record_remark"),
                rs.getString("reason"),
                rs.getString("actor_kind"),
                nullableLong(rs.getObject("platform_admin_id")),
                nullableLong(rs.getObject("staff_account_id")),
                rs.getObject("changed_at", LocalDateTime.class));
    }

    private static Long nullableLong(Object value) {
        return value == null ? null : ((Number) value).longValue();
    }

    private static LocalDateTime nullableTime(LocalDateTime value) {
        return value;
    }

    private static LocalDateTime time(Instant value) {
        return value == null
                ? null
                : LocalDateTime.ofInstant(value, ZoneOffset.UTC);
    }

    private static Instant instant(LocalDateTime value) {
        return value == null
                ? null
                : value.toInstant(ZoneOffset.UTC);
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

    private record ScopeIds(long tenantId, long organizationId) {
    }

    private record ResolvedUser(
            long tenantId,
            long organizationId,
            long organizationUserId) {
    }

    private record PageAnchor(
            long highWatermark,
            LocalDateTime lastSortTime,
            String lastStableKey) {
    }

    private record PageRows(
            List<SummaryRow> items,
            String nextCursor) {
    }

    private record SummaryRow(
            long id,
            String recordNo,
            long operationId,
            UUID operationUid,
            long cleanerId,
            String deviceCode,
            int portNo,
            String removedBag,
            String installedBag,
            LocalDateTime deviceOccurredAt,
            LocalDateTime completedAt,
            LocalDateTime sortAt,
            String recalculatedStatus,
            Long recalculatedWeight,
            Long effectiveWeight,
            String effectiveSource,
            String recordClass,
            String remark,
            long version,
            String photoCompleteness) {
    }

    private record RootRow(
            long id,
            String recordNo,
            long operationId,
            UUID operationUid,
            long cleanerId,
            String deviceCode,
            int portNo,
            UUID eventUid,
            UUID commandUid,
            long configVersion,
            String oldBagState,
            String oldBaselineState,
            String oldBagCode,
            String newBagCode,
            String preUnlockStatus,
            Long preUnlockWeight,
            Long oldBaselineWeight,
            String deviceRemovedStatus,
            Long deviceRemovedWeight,
            String recalculatedStatus,
            Long recalculatedWeight,
            String finalStatus,
            Long finalWeight,
            Long candidateBaselineWeight,
            Long effectiveWeight,
            String effectiveSource,
            String remark,
            long version,
            String recordClass,
            LocalDateTime deviceOccurredAt,
            LocalDateTime backendReceivedAt,
            LocalDateTime completedAt) {
    }

    private record AnomalyRow(
            String code,
            String diagnosticJson,
            LocalDateTime detectedAt) {
    }

    private record PhotoRow(
            String position,
            String status,
            String url,
            LocalDateTime capturedAt,
            String missingReason) {
    }

    private record BaselineRow(
            long version,
            long weight,
            LocalDateTime establishedAt) {
    }

    private record DetectionRow(
            UUID uid,
            String status,
            String finalResult,
            String failureCode,
            LocalDateTime completedAt) {
    }

    private record ChangeRow(
            long id,
            UUID uid,
            long fromVersion,
            long toVersion,
            Long beforeWeight,
            String beforeSource,
            String beforeRemark,
            Long afterWeight,
            String afterSource,
            String afterRemark,
            String reason,
            String actorKind,
            Long platformAdminId,
            Long staffAccountId,
            LocalDateTime changedAt) {
    }

    private record RecordVersion(long id, long version) {
    }

    private record DetailBundle(
            RootRow root,
            List<AnomalyRow> anomalies,
            List<PhotoRow> photos,
            BaselineRow baseline,
            DetectionRow detection,
            ChangeRow latestChange) {
    }

    private record IdentityBundle(
            UUID cleanerUid,
            Map<Long, DeliveryOrderIdentityFacts.ReviewerIdentity> actors) {
    }

    private record Filters(
            String resultKind,
            String deviceCode,
            Integer portNo,
            String removedBag,
            String installedBag,
            String anomalyCode,
            String photoCompleteness,
            LocalDateTime occurredFrom,
            LocalDateTime occurredTo) {

        static Filters empty() {
            return new Filters(
                    null, null, null, null, null,
                    null, null, null, null);
        }

        void append(
                StringBuilder sql,
                MapSqlParameterSource parameters) {
            if (resultKind != null) {
                sql.append(" AND record.record_class = :resultKind");
                parameters.addValue("resultKind", resultKind);
            }
            if (deviceCode != null) {
                sql.append(" AND asset.device_public_code = :deviceCode");
                parameters.addValue("deviceCode", deviceCode);
            }
            if (portNo != null) {
                sql.append(" AND port.port_no = :portNo");
                parameters.addValue("portNo", portNo);
            }
            if (removedBag != null) {
                sql.append(" AND record.old_bag_code_snapshot = :removedBag");
                parameters.addValue("removedBag", removedBag);
            }
            if (installedBag != null) {
                sql.append(" AND record.new_bag_code_snapshot = :installedBag");
                parameters.addValue("installedBag", installedBag);
            }
            if (anomalyCode != null) {
                sql.append("""
                         AND EXISTS (
                             SELECT 1
                             FROM rec_clean_anomaly anomaly
                             WHERE anomaly.clean_record_id = record.id
                               AND anomaly.anomaly_code = :anomalyCode
                         )
                        """);
                parameters.addValue("anomalyCode", anomalyCode);
            }
            if (photoCompleteness != null) {
                String comparison = "COMPLETE".equals(photoCompleteness)
                        ? " = 4"
                        : " <> 4";
                sql.append("""
                         AND (
                             SELECT COUNT(*)
                             FROM rec_clean_photo filtered_photo
                             WHERE filtered_photo.clean_operation_id =
                                   record.clean_operation_id
                               AND filtered_photo.status = 'AVAILABLE'
                         )
                        """).append(comparison);
            }
            if (occurredFrom != null) {
                sql.append("""
                         AND COALESCE(record.device_occurred_at,
                                      record.completed_at) >= :occurredFrom
                        """);
                parameters.addValue("occurredFrom", occurredFrom);
            }
            if (occurredTo != null) {
                sql.append("""
                         AND COALESCE(record.device_occurred_at,
                                      record.completed_at) < :occurredTo
                        """);
                parameters.addValue("occurredTo", occurredTo);
            }
        }

        Map<String, Object> fingerprintFields(
                AuthorizedDeliveryScope authorized,
                UUID cleanerUserUid,
                int limit) {
            Map<String, Object> values = new LinkedHashMap<>();
            values.put("channel", "WEB");
            values.put("principalUid", authorized.principalUid());
            values.put("tenantCode", authorized.tenantCode());
            values.put(
                    "organizationCode",
                    authorized.organizationCode());
            values.put("resultKind", marker(resultKind));
            values.put("cleanerUserUid", marker(cleanerUserUid));
            values.put("deviceCode", marker(deviceCode));
            values.put("portNo", marker(portNo));
            values.put("removedBagQr", marker(removedBag));
            values.put("installedBagQr", marker(installedBag));
            values.put("anomalyCode", marker(anomalyCode));
            values.put(
                    "photoCompleteness",
                    marker(photoCompleteness));
            values.put("occurredFrom", marker(occurredFrom));
            values.put("occurredTo", marker(occurredTo));
            values.put("limit", limit);
            return values;
        }

        private static Object marker(Object value) {
            return value == null ? "<null>" : value;
        }
    }
}
