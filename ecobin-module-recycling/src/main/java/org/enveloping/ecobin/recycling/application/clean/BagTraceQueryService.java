package org.enveloping.ecobin.recycling.application.clean;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.BagTraceIdentityQueryPort;
import org.enveloping.ecobin.identity.api.port.StartCleanIdentityParticipationPort;
import org.enveloping.ecobin.identity.api.result.BagTraceIdentityFacts;
import org.enveloping.ecobin.identity.api.result.LockedMiniappCleanScope;
import org.enveloping.ecobin.device.api.port.RecyclingDeviceRelationQueryPort;
import org.enveloping.ecobin.device.api.port.BagTraceSessionQueryPort;
import org.enveloping.ecobin.device.api.persistence.BagTraceSessionSelectionRef;
import org.enveloping.ecobin.recycling.application.devicefacts.RecyclingDeviceRelationBatch;
import org.enveloping.ecobin.recycling.application.devicefacts.RecyclingDeviceRelationBatch.Entry;
import org.enveloping.ecobin.recycling.application.devicefacts.RecyclingDeviceRelationBatch.Kind;
import org.enveloping.ecobin.recycling.web.v1.BagTraceModels.BagTraceDeliveryOrderDetail;
import org.enveloping.ecobin.recycling.web.v1.BagTraceModels.BagTraceDeliveryOrderItem;
import org.enveloping.ecobin.recycling.web.v1.BagTraceModels.BagTraceDeliveryOrderPage;
import org.enveloping.ecobin.recycling.web.v1.BagTraceModels.BagTracePhoto;
import org.enveloping.ecobin.recycling.web.v1.BagTraceModels.BagTraceUser;
import org.enveloping.ecobin.recycling.web.v1.BagTraceModels.BagUseCycleItem;
import org.enveloping.ecobin.recycling.web.v1.BagTraceModels.BagUseCyclePage;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.Map;
import java.util.UUID;

@Service
public class BagTraceQueryService {

    private static final int DEFAULT_LIMIT = 20;
    private static final int MAX_LIMIT = 100;

    private final JdbcTemplate jdbc;
    private final StartCleanIdentityParticipationPort cleanIdentity;
    private final BagTraceIdentityQueryPort identityFacts;
    private final RecyclingDeviceRelationQueryPort deviceRelations;
    private final BagTraceSessionQueryPort sessions;

    public BagTraceQueryService(
            JdbcTemplate jdbc,
            StartCleanIdentityParticipationPort cleanIdentity,
            BagTraceIdentityQueryPort identityFacts,
            RecyclingDeviceRelationQueryPort deviceRelations,
            BagTraceSessionQueryPort sessions) {
        this.jdbc = jdbc;
        this.cleanIdentity = cleanIdentity;
        this.identityFacts = identityFacts;
        this.deviceRelations = deviceRelations;
        this.sessions = sessions;
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public BagUseCyclePage cycles(
            String bagQr, String cursor, Integer requestedLimit) {
        Scope scope = scope();
        String bag = bag(bagQr);
        int limit = limit(requestedLimit);
        CycleCursor anchor = cycleCursor(bag, cursor);
        List<Object> parameters = new ArrayList<>(List.of(
                scope.tenantId(), scope.organizationId(), bag));
        String anchorSql = "";
        if (anchor != null) {
            anchorSql = " AND (start_event.created_at < ?"
                    + " OR (start_event.created_at = ?"
                    + " AND start_event.id < ?))";
            parameters.add(anchor.createdAt());
            parameters.add(anchor.createdAt());
            parameters.add(anchor.id());
        }
        parameters.add(limit + 1);
        List<CycleRow> rows = jdbc.query("""
                        SELECT start_event.id,
                               start_event.event_uid,
                               start_event.event_type,
                               start_event.created_at AS boundary_start_at,
                               start_event.occurred_at AS installed_at,
                               (
                                 SELECT removal.created_at
                                 FROM rec_bag_occupancy_event removal
                                 WHERE removal.tenant_id = start_event.tenant_id
                                   AND removal.organization_id =
                                       start_event.organization_id
                                   AND removal.bag_id = start_event.bag_id
                                   AND removal.port_id = start_event.port_id
                                   AND removal.event_type = 'REMOVED_BY_CLEAN'
                                   AND removal.created_at >= start_event.created_at
                                 ORDER BY removal.created_at, removal.id
                                 LIMIT 1
                               ) AS boundary_end_at,
                               (
                                 SELECT removal.occurred_at
                                 FROM rec_bag_occupancy_event removal
                                 WHERE removal.tenant_id = start_event.tenant_id
                                   AND removal.organization_id =
                                       start_event.organization_id
                                   AND removal.bag_id = start_event.bag_id
                                   AND removal.port_id = start_event.port_id
                                   AND removal.event_type = 'REMOVED_BY_CLEAN'
                                   AND removal.created_at >= start_event.created_at
                                 ORDER BY removal.created_at, removal.id
                                 LIMIT 1
                               ) AS removed_at,
                               0 AS delivery_count,
                               start_event.bag_id,
                               start_event.port_id
                        FROM rec_bag bag
                        JOIN rec_bag_occupancy_event start_event
                          ON start_event.tenant_id = bag.tenant_id
                         AND start_event.organization_id = bag.organization_id
                         AND start_event.bag_id = bag.id
                         AND start_event.event_type IN (
                             'INITIAL_INSTALLED', 'INSTALLED_BY_CLEAN'
                         )
                        WHERE bag.tenant_id = ?
                          AND bag.organization_id = ?
                          AND bag.bag_code = ?
                        """ + anchorSql + """
                        ORDER BY start_event.created_at DESC, start_event.id DESC
                        LIMIT ?
                        """,
                (rs, ignored) -> cycleRow(rs),
                parameters.toArray());
        if (rows.isEmpty() && !bagExists(scope, bag)) {
            throw notFound();
        }
        boolean more = rows.size() > limit;
        List<CycleRow> visible = more
                ? rows.subList(0, limit) : rows;
        List<CycleRow> enriched = enrichCycles(scope, visible);
        return new BagUseCyclePage(
                bag,
                "OPAQUE_V1",
                0,
                enriched.stream().map(this::cycleItem).toList(),
                databaseNow(),
                more ? encodeCycle(bag, visible.getLast()) : null);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public BagTraceDeliveryOrderPage orders(
            String bagQr,
            UUID cycleUid,
            String cursor,
            Integer requestedLimit) {
        Scope scope = scope();
        String bag = bag(bagQr);
        CycleRow cycle = cycle(scope, bag, cycleUid);
        int limit = limit(requestedLimit);
        OrderCursor anchor = orderCursor(bag, cycleUid, cursor);
        List<OrderRow> rows = orderRows(
                scope, cycle, null, anchor, limit + 1);
        boolean more = rows.size() > limit;
        List<OrderRow> visible = more
                ? rows.subList(0, limit) : rows;
        Map<UUID, BagTraceIdentityFacts.User> users = users(visible);
        return new BagTraceDeliveryOrderPage(
                bag,
                cycleUid,
                visible.stream().map(row -> item(row, users)).toList(),
                databaseNow(),
                more ? encodeOrder(bag, cycleUid, visible.getLast()) : null);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public BagTraceDeliveryOrderDetail order(
            String bagQr,
            UUID cycleUid,
            String requestedOrderNo) {
        Scope scope = scope();
        String bag = bag(bagQr);
        String orderNo = orderNo(requestedOrderNo);
        CycleRow cycle = cycle(scope, bag, cycleUid);
        OrderRow row = orderRows(scope, cycle, orderNo, null, 1)
                .stream().findFirst().orElseThrow(
                        BagTraceQueryService::notFound);
        Map<UUID, BagTraceIdentityFacts.User> users = users(List.of(row));
        List<BagTracePhoto> photos = jdbc.query("""
                        SELECT photo.position, photo.status,
                               photo.object_url, photo.captured_at,
                               photo.missing_reason
                        FROM rec_delivery_photo photo
                        WHERE photo.tenant_id = ?
                          AND photo.organization_id = ?
                          AND photo.delivery_order_id = ?
                        ORDER BY FIELD(photo.position,
                            'BEFORE_INNER', 'BEFORE_OUTER',
                            'AFTER_INNER', 'AFTER_OUTER')
                        """,
                (rs, ignored) -> new BagTracePhoto(
                        rs.getString("position"),
                        rs.getString("status"),
                        rs.getString("object_url"),
                        instant(rs.getObject("captured_at", LocalDateTime.class)),
                        rs.getString("missing_reason")),
                scope.tenantId(), scope.organizationId(), row.id());
        return new BagTraceDeliveryOrderDetail(
                bag, cycleUid, item(row, users), photos);
    }

    private Scope scope() {
        LockedMiniappCleanScope current = cleanIdentity
                .lockCurrentMiniappScope();
        Scope result = current.organizationScopeRef()
                .withOrganizationScopeOnce(Scope::new);
        cleanIdentity.lockCurrentCleaner(current);
        return result;
    }

    private CycleRow cycle(Scope scope, String bag, UUID cycleUid) {
        if (cycleUid == null) {
            throw notFound();
        }
        return jdbc.query("""
                        SELECT start_event.id,
                               start_event.event_uid,
                               start_event.event_type,
                               start_event.created_at AS boundary_start_at,
                               start_event.occurred_at AS installed_at,
                               (
                                 SELECT removal.created_at
                                 FROM rec_bag_occupancy_event removal
                                 WHERE removal.tenant_id = start_event.tenant_id
                                   AND removal.organization_id =
                                       start_event.organization_id
                                   AND removal.bag_id = start_event.bag_id
                                   AND removal.port_id = start_event.port_id
                                   AND removal.event_type = 'REMOVED_BY_CLEAN'
                                   AND removal.created_at >= start_event.created_at
                                 ORDER BY removal.created_at, removal.id
                                 LIMIT 1
                               ) AS boundary_end_at,
                               (
                                 SELECT removal.occurred_at
                                 FROM rec_bag_occupancy_event removal
                                 WHERE removal.tenant_id = start_event.tenant_id
                                   AND removal.organization_id =
                                       start_event.organization_id
                                   AND removal.bag_id = start_event.bag_id
                                   AND removal.port_id = start_event.port_id
                                   AND removal.event_type = 'REMOVED_BY_CLEAN'
                                   AND removal.created_at >= start_event.created_at
                                 ORDER BY removal.created_at, removal.id
                                 LIMIT 1
                               ) AS removed_at,
                               0 AS delivery_count,
                               start_event.bag_id,
                               start_event.port_id
                        FROM rec_bag bag
                        JOIN rec_bag_occupancy_event start_event
                          ON start_event.tenant_id = bag.tenant_id
                         AND start_event.organization_id = bag.organization_id
                         AND start_event.bag_id = bag.id
                        WHERE bag.tenant_id = ?
                          AND bag.organization_id = ?
                          AND bag.bag_code = ?
                          AND start_event.event_uid = ?
                          AND start_event.event_type IN (
                              'INITIAL_INSTALLED', 'INSTALLED_BY_CLEAN'
                          )
                        """,
                (rs, ignored) -> cycleRow(rs),
                scope.tenantId(), scope.organizationId(), bag,
                cycleUid.toString()).stream().findFirst()
                .map(row -> enrichCycles(scope, List.of(row)).getFirst())
                .orElseThrow(BagTraceQueryService::notFound);
    }

    private List<OrderRow> orderRows(
            Scope scope,
            CycleRow cycle,
            String orderNo,
            OrderCursor anchor,
            int limit) {
        return cycleSessions(scope, cycle).consumeOnce(selected ->
                orderRowsOwned(scope, cycle, selected, orderNo, anchor, limit));
    }

    private List<OrderRow> orderRowsOwned(
            Scope scope,
            CycleRow cycle,
            List<BagTraceSessionSelectionRef.Session> selected,
            String orderNo,
            OrderCursor anchor,
            int limit) {
        if (selected.isEmpty()) {
            return List.of();
        }
        StringBuilder selectedSql = new StringBuilder(
                "SELECT ? session_id, ? created_at");
        selectedSql.append(" UNION ALL SELECT ?, ?"
                .repeat(selected.size() - 1));
        StringBuilder sql = new StringBuilder(
                "WITH selected_session AS (" + selectedSql + ") " + """
                SELECT delivery_order.id,
                       delivery_order.delivery_order_no,
                       delivery_order.tenant_id,
                       delivery_order.organization_id,
                       delivery_order.organization_user_id,
                       delivery_order.device_occurred_at,
                       delivery_order.backend_received_at,
                       delivery_order.raw_business_weight_kg,
                       delivery_order.raw_amount_cent,
                       delivery_order.final_business_weight_kg,
                       delivery_order.final_amount_cent,
                       delivery_order.review_status,
                       revision.reason,
                       selected_session.created_at AS session_created_at,
                       (
                         SELECT COUNT(*)
                         FROM rec_delivery_photo photo
                         WHERE photo.delivery_order_id = delivery_order.id
                           AND photo.status = 'AVAILABLE'
                       ) AS available_photo_count
                FROM rec_delivery_order delivery_order
                JOIN selected_session
                  ON selected_session.session_id =
                     delivery_order.delivery_session_id
                LEFT JOIN rec_delivery_revision revision
                  ON revision.id = delivery_order.current_revision_id
                WHERE delivery_order.tenant_id = ?
                  AND delivery_order.organization_id = ?
                  AND delivery_order.bag_id = ?
                  AND delivery_order.port_id = ?
                """);
        List<Object> args = new ArrayList<>();
        for (BagTraceSessionSelectionRef.Session session : selected) {
            args.add(session.key());
            args.add(LocalDateTime.ofInstant(
                    session.createdAt(), ZoneOffset.UTC));
        }
        args.add(scope.tenantId());
        args.add(scope.organizationId());
        args.add(cycle.bagId());
        args.add(cycle.portId());
        if (orderNo != null) {
            sql.append(" AND delivery_order.delivery_order_no = ?");
            args.add(orderNo);
        }
        if (anchor != null) {
            sql.append("""
                     AND (selected_session.created_at < ?
                          OR (selected_session.created_at = ?
                              AND delivery_order.id < ?))
                    """);
            args.add(anchor.createdAt());
            args.add(anchor.createdAt());
            args.add(anchor.id());
        }
        sql.append("""
                 ORDER BY selected_session.created_at DESC,
                          delivery_order.id DESC
                 LIMIT ?
                """);
        args.add(limit);
        return jdbc.query(sql.toString(),
                (rs, ignored) -> orderRow(rs), args.toArray());
    }

    private BagTraceSessionSelectionRef cycleSessions(
            Scope scope, CycleRow cycle) {
        return sessions.sessions(new RecyclingBagTraceCycleRef(
                scope.tenantId(), scope.organizationId(), cycle.portId(),
                instant(cycle.boundaryStartAt()),
                instant(cycle.boundaryEndAt())));
    }

    private Map<UUID, BagTraceIdentityFacts.User> users(
            List<OrderRow> rows) {
        if (rows.isEmpty()) {
            return Map.of();
        }
        return identityFacts.resolve(new BagTraceIdentityBatch(
                rows.stream().map(row -> new BagTraceIdentityBatch.Entry(
                        row.identityToken(), row.tenantId(),
                        row.organizationId(), row.organizationUserId()))
                        .toList())).users();
    }

    private BagTraceDeliveryOrderItem item(
            OrderRow row,
            Map<UUID, BagTraceIdentityFacts.User> users) {
        BagTraceIdentityFacts.User user = users.get(row.identityToken());
        if (user == null) {
            throw new IllegalStateException("bag trace user facts missing");
        }
        return new BagTraceDeliveryOrderItem(
                row.orderNo(),
                new BagTraceUser(user.organizationUserUid(),
                        user.nickname(), user.maskedPhoneNumber()),
                instant(row.deviceOccurredAt()),
                instant(row.backendReceivedAt()),
                decimal(row.rawWeight()),
                yuan(row.rawAmountCent()),
                decimal(row.finalWeight()),
                yuan(row.finalAmountCent()),
                row.reviewStatus(),
                row.reason(),
                row.availablePhotoCount() == 4
                        ? "COMPLETE" : "INCOMPLETE");
    }

    private BagUseCycleItem cycleItem(CycleRow row) {
        return new BagUseCycleItem(
                row.uid(),
                row.boundaryEndAt() == null ? "ACTIVE" : "CLOSED",
                row.eventType(),
                row.deviceCode(),
                row.portNo(),
                instant(row.installedAt()),
                instant(row.removedAt()),
                row.deliveryCount());
    }

    private List<CycleRow> enrichCycles(
            Scope scope, List<CycleRow> rows) {
        if (rows.isEmpty()) {
            return List.of();
        }
        var facts = deviceRelations.resolve(new RecyclingDeviceRelationBatch(
                rows.stream().map(row -> new Entry(
                        row.portToken(), Kind.PORT,
                        scope.tenantId(), scope.organizationId(),
                        row.portId())).toList()));
        return rows.stream().map(row -> {
            var port = facts.ports().get(row.portToken());
            if (port == null) {
                throw new IllegalStateException(
                        "bag trace port facts missing");
            }
            return new CycleRow(
                    row.id(), row.uid(), row.eventType(),
                    row.boundaryStartAt(), row.boundaryEndAt(),
                    row.installedAt(), row.removedAt(),
                    port.deviceCode(), port.portNo(),
                    deliveryCount(scope, row), row.portToken(),
                    row.bagId(), row.portId());
        }).toList();
    }

    private long deliveryCount(Scope scope, CycleRow cycle) {
        return cycleSessions(scope, cycle).consumeOnce(selected -> {
            if (selected.isEmpty()) {
                return 0L;
            }
            String placeholders = "?,".repeat(selected.size());
            placeholders = placeholders.substring(0, placeholders.length() - 1);
            List<Object> args = new ArrayList<>();
            args.add(scope.tenantId());
            args.add(scope.organizationId());
            args.add(cycle.bagId());
            args.add(cycle.portId());
            args.addAll(selected.stream()
                    .map(BagTraceSessionSelectionRef.Session::key).toList());
            Long count = jdbc.queryForObject("""
                            SELECT COUNT(*)
                            FROM rec_delivery_order
                            WHERE tenant_id = ? AND organization_id = ?
                              AND bag_id = ? AND port_id = ?
                              AND delivery_session_id IN (
                            """ + placeholders + ")",
                    Long.class, args.toArray());
            return count == null ? 0L : count;
        });
    }

    private boolean bagExists(Scope scope, String bag) {
        Integer count = jdbc.queryForObject("""
                        SELECT COUNT(*) FROM rec_bag
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND bag_code = ?
                        """, Integer.class,
                scope.tenantId(), scope.organizationId(), bag);
        return count != null && count == 1;
    }

    private Instant databaseNow() {
        LocalDateTime value = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        if (value == null) {
            throw new IllegalStateException("database time unavailable");
        }
        return instant(value);
    }

    private static CycleRow cycleRow(ResultSet rs) throws SQLException {
        return new CycleRow(
                rs.getLong("id"),
                UUID.fromString(rs.getString("event_uid")),
                rs.getString("event_type"),
                rs.getObject("boundary_start_at", LocalDateTime.class),
                rs.getObject("boundary_end_at", LocalDateTime.class),
                rs.getObject("installed_at", LocalDateTime.class),
                rs.getObject("removed_at", LocalDateTime.class),
                null,
                0,
                rs.getLong("delivery_count"),
                UUID.randomUUID(),
                optionalLong(rs, "bag_id"),
                optionalLong(rs, "port_id"));
    }

    private static OrderRow orderRow(ResultSet rs) throws SQLException {
        return new OrderRow(
                rs.getLong("id"),
                rs.getString("delivery_order_no"),
                UUID.randomUUID(),
                rs.getLong("organization_user_id"),
                rs.getLong("tenant_id"),
                rs.getLong("organization_id"),
                rs.getObject("device_occurred_at", LocalDateTime.class),
                rs.getObject("backend_received_at", LocalDateTime.class),
                rs.getBigDecimal("raw_business_weight_kg"),
                (Long) rs.getObject("raw_amount_cent"),
                rs.getBigDecimal("final_business_weight_kg"),
                (Long) rs.getObject("final_amount_cent"),
                rs.getString("review_status"),
                rs.getString("reason"),
                rs.getObject("session_created_at", LocalDateTime.class),
                rs.getInt("available_photo_count"));
    }

    private static Long optionalLong(ResultSet rs, String column) {
        try {
            return (Long) rs.getObject(column);
        } catch (SQLException ignored) {
            return null;
        }
    }

    private static String bag(String value) {
        if (value == null
                || !value.trim().matches("[A-Za-z0-9_-]{8,64}")) {
            throw notFound();
        }
        return value.trim();
    }

    private static String orderNo(String value) {
        if (value == null || value.isBlank() || value.length() > 64) {
            throw notFound();
        }
        return value.trim();
    }

    private static int limit(Integer value) {
        if (value == null) {
            return DEFAULT_LIMIT;
        }
        if (value < 1 || value > MAX_LIMIT) {
            throw new TargetApiException(
                    400, "COMMON.VALIDATION_FAILED",
                    "limit 必须在 1 到 100 之间");
        }
        return value;
    }

    private static String encodeCycle(String bag, CycleRow row) {
        return encode("C|" + bag + "|"
                + row.boundaryStartAt() + "|" + row.id());
    }

    private static CycleCursor cycleCursor(String bag, String cursor) {
        if (cursor == null || cursor.isBlank()) {
            return null;
        }
        try {
            String[] parts = decode(cursor).split("\\|", -1);
            if (parts.length != 4 || !"C".equals(parts[0])
                    || !bag.equals(parts[1])) {
                throw new IllegalArgumentException();
            }
            return new CycleCursor(
                    LocalDateTime.parse(parts[2]),
                    Long.parseLong(parts[3]));
        } catch (Exception exception) {
            throw invalidCursor();
        }
    }

    private static String encodeOrder(
            String bag, UUID cycleUid, OrderRow row) {
        return encode("O|" + bag + "|" + cycleUid + "|"
                + row.sessionCreatedAt() + "|" + row.id());
    }

    private static OrderCursor orderCursor(
            String bag, UUID cycleUid, String cursor) {
        if (cursor == null || cursor.isBlank()) {
            return null;
        }
        try {
            String[] parts = decode(cursor).split("\\|", -1);
            if (parts.length != 5 || !"O".equals(parts[0])
                    || !bag.equals(parts[1])
                    || !cycleUid.toString().equals(parts[2])) {
                throw new IllegalArgumentException();
            }
            return new OrderCursor(
                    LocalDateTime.parse(parts[3]),
                    Long.parseLong(parts[4]));
        } catch (Exception exception) {
            throw invalidCursor();
        }
    }

    private static String encode(String value) {
        return Base64.getUrlEncoder().withoutPadding().encodeToString(
                value.getBytes(StandardCharsets.UTF_8));
    }

    private static String decode(String value) {
        return new String(Base64.getUrlDecoder().decode(value),
                StandardCharsets.UTF_8);
    }

    private static String decimal(BigDecimal value) {
        return value == null ? null : value.setScale(2).toPlainString();
    }

    private static String yuan(Long cent) {
        return cent == null ? null
                : BigDecimal.valueOf(cent, 2).toPlainString();
    }

    private static Instant instant(LocalDateTime value) {
        return value == null ? null : value.toInstant(ZoneOffset.UTC);
    }

    private static TargetApiException invalidCursor() {
        return new TargetApiException(
                400, "COMMON.INVALID_CURSOR", "分页游标无效或已过期");
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404, "RESOURCE.NOT_FOUND", "资源不存在");
    }

    private record Scope(long tenantId, long organizationId) { }
    private record CycleCursor(LocalDateTime createdAt, long id) { }
    private record OrderCursor(LocalDateTime createdAt, long id) { }
    private record CycleRow(
            long id,
            UUID uid,
            String eventType,
            LocalDateTime boundaryStartAt,
            LocalDateTime boundaryEndAt,
            LocalDateTime installedAt,
            LocalDateTime removedAt,
            String deviceCode,
            int portNo,
            long deliveryCount,
            UUID portToken,
            Long bagId,
            Long portId) { }
    private record OrderRow(
            long id,
            String orderNo,
            UUID identityToken,
            long organizationUserId,
            long tenantId,
            long organizationId,
            LocalDateTime deviceOccurredAt,
            LocalDateTime backendReceivedAt,
            BigDecimal rawWeight,
            Long rawAmountCent,
            BigDecimal finalWeight,
            Long finalAmountCent,
            String reviewStatus,
            String reason,
            LocalDateTime sessionCreatedAt,
            int availablePhotoCount) { }
}
