package org.enveloping.ecobin.recycling.application.clean;

import org.enveloping.ecobin.device.api.port.RecyclingDeviceRelationQueryPort;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.persistence.ManagementScopePersistenceRef;
import org.enveloping.ecobin.identity.api.port.BagTraceIdentityQueryPort;
import org.enveloping.ecobin.identity.api.port.ManagementScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.query.ManagementScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedManagementScope;
import org.enveloping.ecobin.identity.api.result.BagTraceIdentityFacts;
import org.enveloping.ecobin.recycling.application.devicefacts.RecyclingDeviceRelationBatch;
import org.enveloping.ecobin.recycling.web.v1.BagTraceModels.BagCleanRecordItem;
import org.enveloping.ecobin.recycling.web.v1.BagTraceModels.BagCleanRecordPage;
import org.enveloping.ecobin.recycling.web.v1.BagTraceModels.BagCurrentOccupancy;
import org.enveloping.ecobin.recycling.web.v1.BagTraceModels.BagOccupancyEventItem;
import org.enveloping.ecobin.recycling.web.v1.BagTraceModels.BagOccupancyEventPage;
import org.enveloping.ecobin.recycling.web.v1.BagTraceModels.BagTraceDeliveryOrderItem;
import org.enveloping.ecobin.recycling.web.v1.BagTraceModels.BagTraceUser;
import org.enveloping.ecobin.recycling.web.v1.BagTraceModels.WebBagDeliveryOrderPage;
import org.enveloping.ecobin.recycling.web.v1.BagTraceModels.WebBagDetail;
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

import static org.enveloping.ecobin.identity.api.persistence.ManagementScopePersistenceRef.Purpose.RECYCLING_BAG_TRACE_QUERY;
import static org.enveloping.ecobin.identity.api.query.ManagementScopeAuthorizationQuery.Channel.WEB;

/** Web and platform bag trace projections, scoped by real-time capabilities. */
@Service
public class WebBagTraceQueryService {

    private static final int DEFAULT_LIMIT = 20;
    private static final int MAX_LIMIT = 100;

    private final JdbcTemplate jdbc;
    private final ManagementScopeAuthorizationPort authorization;
    private final RecyclingDeviceRelationQueryPort deviceRelations;
    private final BagTraceIdentityQueryPort identityFacts;

    public WebBagTraceQueryService(
            JdbcTemplate jdbc,
            ManagementScopeAuthorizationPort authorization,
            RecyclingDeviceRelationQueryPort deviceRelations,
            BagTraceIdentityQueryPort identityFacts) {
        this.jdbc = jdbc;
        this.authorization = authorization;
        this.deviceRelations = deviceRelations;
        this.identityFacts = identityFacts;
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public WebBagDetail detail(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String requestedBagQr) {
        String bagQr = bag(requestedBagQr);
        AuthorizedManagementScope authorized = authorize(
                platformPath, tenantCode, organizationCode, "clean.read");
        return authorized.persistenceRef().withScopeOnce(
                RECYCLING_BAG_TRACE_QUERY,
                (tenantKey, organizations, ignoredPlatform, ignoredStaff) -> {
                    Scope scope = exactScope(tenantKey, organizations);
                    BagRow row = bagRow(scope, bagQr);
                    PortFact port = row.portId() == null
                            ? null : port(scope, row.portId());
                    return new WebBagDetail(
                            bagQr,
                            instant(row.registeredAt()),
                            new BagCurrentOccupancy(
                                    row.occupancyType() == null
                                            ? "NONE" : row.occupancyType(),
                                    port == null ? null : port.deviceCode(),
                                    port == null ? null : port.portNo(),
                                    row.operationUid(),
                                    instant(row.acquiredAt())),
                            instant(row.lastRelationChangedAt()));
                });
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public BagOccupancyEventPage events(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String requestedBagQr,
            String cursor,
            Integer requestedLimit) {
        String bagQr = bag(requestedBagQr);
        int limit = limit(requestedLimit);
        EventCursor anchor = eventCursor(bagQr, cursor);
        AuthorizedManagementScope authorized = authorize(
                platformPath, tenantCode, organizationCode, "clean.read");
        return authorized.persistenceRef().withScopeOnce(
                RECYCLING_BAG_TRACE_QUERY,
                (tenantKey, organizations, ignoredPlatform, ignoredStaff) -> {
                    Scope scope = exactScope(tenantKey, organizations);
                    long bagId = bagId(scope, bagQr);
                    List<Object> args = new ArrayList<>(List.of(
                            scope.tenantId(), scope.organizationId(), bagId));
                    String anchorSql = "";
                    if (anchor != null) {
                        anchorSql = " AND (event.occurred_at < ?"
                                + " OR (event.occurred_at = ?"
                                + " AND event.event_uid < ?))";
                        args.add(anchor.occurredAt());
                        args.add(anchor.occurredAt());
                        args.add(anchor.eventUid().toString());
                    }
                    args.add(limit + 1);
                    List<EventRow> rows = jdbc.query("""
                                    SELECT event.event_uid, event.event_type,
                                           event.port_id,
                                           operation.operation_uid,
                                           event.occurred_at
                                    FROM rec_bag_occupancy_event event
                                    LEFT JOIN rec_clean_operation operation
                                      ON operation.tenant_id = event.tenant_id
                                     AND operation.organization_id =
                                         event.organization_id
                                     AND operation.id = event.clean_operation_id
                                    WHERE event.tenant_id = ?
                                      AND event.organization_id = ?
                                      AND event.bag_id = ?
                                    """ + anchorSql + """
                                    ORDER BY event.occurred_at DESC,
                                             event.event_uid DESC
                                    LIMIT ?
                                    """,
                            (rs, ignored) -> eventRow(rs), args.toArray());
                    boolean more = rows.size() > limit;
                    List<EventRow> visible = more
                            ? rows.subList(0, limit) : rows;
                    Map<UUID, PortFact> ports = ports(scope, visible.stream()
                            .map(row -> new PortRequest(
                                    row.portToken(), row.portId())).toList());
                    List<BagOccupancyEventItem> items = visible.stream()
                            .map(row -> eventItem(row, ports)).toList();
                    return new BagOccupancyEventPage(
                            bagQr,
                            items,
                            databaseNow(),
                            more ? encodeEvent(bagQr, visible.getLast()) : null);
                });
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public BagCleanRecordPage cleanRecords(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String requestedBagQr,
            String cursor,
            Integer requestedLimit) {
        String bagQr = bag(requestedBagQr);
        int limit = limit(requestedLimit);
        RecordCursor anchor = recordCursor(bagQr, cursor);
        AuthorizedManagementScope authorized = authorize(
                platformPath, tenantCode, organizationCode, "clean.read");
        return authorized.persistenceRef().withScopeOnce(
                RECYCLING_BAG_TRACE_QUERY,
                (tenantKey, organizations, ignoredPlatform, ignoredStaff) -> {
                    Scope scope = exactScope(tenantKey, organizations);
                    bagId(scope, bagQr);
                    List<Object> args = new ArrayList<>(List.of(
                            scope.tenantId(), scope.organizationId(),
                            bagQr, bagQr));
                    String anchorSql = "";
                    if (anchor != null) {
                        anchorSql = " AND (record.device_occurred_at < ?"
                                + " OR (record.device_occurred_at = ?"
                                + " AND record.clean_record_no < ?))";
                        args.add(anchor.completedAt());
                        args.add(anchor.completedAt());
                        args.add(anchor.recordNo());
                    }
                    args.add(limit + 1);
                    List<CleanRow> rows = jdbc.query("""
                                    SELECT record.clean_record_no,
                                           operation.operation_uid,
                                           record.cleaner_organization_user_id,
                                           record.port_id,
                                           record.old_bag_code_snapshot,
                                           record.new_bag_code_snapshot,
                                           record.device_occurred_at,
                                           record.recalculated_removed_net_weight_status,
                                           record.recalculated_removed_net_weight_g,
                                           record.effective_removed_net_weight_g,
                                           record.effective_weight_source,
                                           record.record_class,
                                           record.record_remark,
                                           record.lock_version,
                                           (SELECT COUNT(*)
                                              FROM rec_clean_photo photo
                                             WHERE photo.clean_operation_id =
                                                   record.clean_operation_id
                                               AND photo.status = 'AVAILABLE')
                                               AS available_photo_count
                                    FROM rec_clean_record record
                                    JOIN rec_clean_operation operation
                                      ON operation.tenant_id = record.tenant_id
                                     AND operation.organization_id =
                                         record.organization_id
                                     AND operation.id = record.clean_operation_id
                                    WHERE record.tenant_id = ?
                                      AND record.organization_id = ?
                                      AND (record.old_bag_code_snapshot = ?
                                           OR record.new_bag_code_snapshot = ?)
                                    """ + anchorSql + """
                                    ORDER BY record.device_occurred_at DESC,
                                             record.clean_record_no DESC
                                    LIMIT ?
                                    """,
                            (rs, ignored) -> cleanRow(rs), args.toArray());
                    boolean more = rows.size() > limit;
                    List<CleanRow> visible = more
                            ? rows.subList(0, limit) : rows;
                    Map<UUID, PortFact> ports = ports(scope, visible.stream()
                            .map(row -> new PortRequest(
                                    row.portToken(), row.portId())).toList());
                    Map<UUID, BagTraceIdentityFacts.User> users = users(
                            scope, visible);
                    List<BagCleanRecordItem> items = visible.stream()
                            .map(row -> cleanItem(bagQr, row, ports, users))
                            .toList();
                    return new BagCleanRecordPage(
                            bagQr,
                            items,
                            databaseNow(),
                            more ? encodeRecord(bagQr, visible.getLast()) : null);
                });
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public WebBagDeliveryOrderPage deliveryOrders(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String requestedBagQr,
            String cursor,
            Integer requestedLimit) {
        String bagQr = bag(requestedBagQr);
        int limit = limit(requestedLimit);
        OrderCursor anchor = orderCursor(bagQr, cursor);
        AuthorizedManagementScope clean = authorize(
                platformPath, tenantCode, organizationCode, "clean.read");
        AuthorizedManagementScope delivery = authorize(
                platformPath, tenantCode, organizationCode, "delivery.read");
        verifySamePublicScope(clean, delivery);
        delivery.persistenceRef().withScopeOnce(
                RECYCLING_BAG_TRACE_QUERY,
                (tenantKey, organizations, ignoredPlatform, ignoredStaff) -> {
                    exactScope(tenantKey, organizations);
                    return null;
                });
        return clean.persistenceRef().withScopeOnce(
                RECYCLING_BAG_TRACE_QUERY,
                (tenantKey, organizations, ignoredPlatform, ignoredStaff) -> {
                    Scope scope = exactScope(tenantKey, organizations);
                    bagId(scope, bagQr);
                    List<Object> args = new ArrayList<>(List.of(
                            scope.tenantId(), scope.organizationId(), bagQr));
                    String anchorSql = "";
                    if (anchor != null) {
                        anchorSql = " AND (delivery_order.backend_received_at < ?"
                                + " OR (delivery_order.backend_received_at = ?"
                                + " AND delivery_order.delivery_order_no < ?))";
                        args.add(anchor.receivedAt());
                        args.add(anchor.receivedAt());
                        args.add(anchor.orderNo());
                    }
                    args.add(limit + 1);
                    List<OrderRow> rows = jdbc.query("""
                                    SELECT delivery_order.delivery_order_no,
                                           delivery_order.organization_user_id,
                                           delivery_order.device_occurred_at,
                                           delivery_order.backend_received_at,
                                           delivery_order.raw_business_weight_kg,
                                           delivery_order.raw_amount_cent,
                                           delivery_order.final_business_weight_kg,
                                           delivery_order.final_amount_cent,
                                           delivery_order.review_status,
                                           revision.reason,
                                           (SELECT COUNT(*)
                                              FROM rec_delivery_photo photo
                                             WHERE photo.delivery_order_id =
                                                   delivery_order.id
                                               AND photo.status = 'AVAILABLE')
                                               AS available_photo_count
                                    FROM rec_delivery_order delivery_order
                                    LEFT JOIN rec_delivery_revision revision
                                      ON revision.id =
                                         delivery_order.current_revision_id
                                    WHERE delivery_order.tenant_id = ?
                                      AND delivery_order.organization_id = ?
                                      AND delivery_order.bag_code_snapshot = ?
                                    """ + anchorSql + """
                                    ORDER BY delivery_order.backend_received_at DESC,
                                             delivery_order.delivery_order_no DESC
                                    LIMIT ?
                                    """,
                            (rs, ignored) -> orderRow(rs), args.toArray());
                    boolean more = rows.size() > limit;
                    List<OrderRow> visible = more
                            ? rows.subList(0, limit) : rows;
                    Map<UUID, BagTraceIdentityFacts.User> users = orderUsers(
                            scope, visible);
                    return new WebBagDeliveryOrderPage(
                            bagQr,
                            visible.stream().map(row -> orderItem(row, users))
                                    .toList(),
                            databaseNow(),
                            more ? encodeOrder(bagQr, visible.getLast()) : null);
                });
    }

    private AuthorizedManagementScope authorize(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String capability) {
        return authorization.authorize(new ManagementScopeAuthorizationQuery(
                WEB, platformPath, tenantCode, organizationCode, capability));
    }

    private static Scope exactScope(
            Long tenantKey,
            List<ManagementScopePersistenceRef.OrganizationKey> organizations) {
        if (tenantKey == null || organizations.size() != 1) {
            throw notFound();
        }
        return new Scope(tenantKey, organizations.getFirst().value());
    }

    private static void verifySamePublicScope(
            AuthorizedManagementScope left,
            AuthorizedManagementScope right) {
        if (!java.util.Objects.equals(left.tenantCode(), right.tenantCode())
                || left.organizations().size() != 1
                || right.organizations().size() != 1
                || !left.organizations().getFirst().code().equals(
                right.organizations().getFirst().code())) {
            throw notFound();
        }
    }

    private BagRow bagRow(Scope scope, String bagQr) {
        return jdbc.query("""
                        SELECT bag.id, bag.registered_at,
                               occupancy.occupancy_type,
                               COALESCE(occupancy.port_id,
                                        operation.port_id) AS relation_port_id,
                               operation.operation_uid,
                               occupancy.acquired_at,
                               (SELECT MAX(event.occurred_at)
                                  FROM rec_bag_occupancy_event event
                                 WHERE event.tenant_id = bag.tenant_id
                                   AND event.organization_id =
                                       bag.organization_id
                                   AND event.bag_id = bag.id)
                                   AS last_relation_changed_at
                        FROM rec_bag bag
                        LEFT JOIN rec_bag_current_occupancy occupancy
                          ON occupancy.tenant_id = bag.tenant_id
                         AND occupancy.organization_id = bag.organization_id
                         AND occupancy.bag_id = bag.id
                        LEFT JOIN rec_clean_operation operation
                          ON operation.tenant_id = occupancy.tenant_id
                         AND operation.organization_id =
                             occupancy.organization_id
                         AND operation.id = occupancy.clean_operation_id
                        WHERE bag.tenant_id = ?
                          AND bag.organization_id = ?
                          AND bag.bag_code = ?
                        """,
                (rs, ignored) -> new BagRow(
                        rs.getLong("id"),
                        rs.getObject("registered_at", LocalDateTime.class),
                        rs.getString("occupancy_type"),
                        nullableLong(rs, "relation_port_id"),
                        nullableUuid(rs, "operation_uid"),
                        rs.getObject("acquired_at", LocalDateTime.class),
                        rs.getObject("last_relation_changed_at",
                                LocalDateTime.class)),
                scope.tenantId(), scope.organizationId(), bagQr)
                .stream().findFirst().orElseThrow(
                        WebBagTraceQueryService::notFound);
    }

    private long bagId(Scope scope, String bagQr) {
        return bagRow(scope, bagQr).id();
    }

    private PortFact port(Scope scope, long portId) {
        UUID token = UUID.randomUUID();
        var facts = deviceRelations.resolve(new RecyclingDeviceRelationBatch(
                List.of(new RecyclingDeviceRelationBatch.Entry(
                        token,
                        RecyclingDeviceRelationBatch.Kind.PORT,
                        scope.tenantId(), scope.organizationId(), portId))));
        var value = facts.ports().get(token);
        if (value == null) {
            throw new IllegalStateException("bag trace port facts missing");
        }
        return new PortFact(value.deviceCode(), value.portNo());
    }

    private Map<UUID, PortFact> ports(
            Scope scope, List<PortRequest> requests) {
        if (requests.isEmpty()) {
            return Map.of();
        }
        var facts = deviceRelations.resolve(new RecyclingDeviceRelationBatch(
                requests.stream().map(request ->
                        new RecyclingDeviceRelationBatch.Entry(
                                request.token(),
                                RecyclingDeviceRelationBatch.Kind.PORT,
                                scope.tenantId(), scope.organizationId(),
                                request.portId())).toList()));
        java.util.HashMap<UUID, PortFact> result = new java.util.HashMap<>();
        requests.forEach(request -> {
            var value = facts.ports().get(request.token());
            if (value == null) {
                throw new IllegalStateException("bag trace port facts missing");
            }
            result.put(request.token(), new PortFact(
                    value.deviceCode(), value.portNo()));
        });
        return Map.copyOf(result);
    }

    private Map<UUID, BagTraceIdentityFacts.User> users(
            Scope scope, List<CleanRow> rows) {
        return resolveUsers(scope, rows.stream()
                .map(row -> new UserRequest(
                        row.userToken(), row.organizationUserId())).toList());
    }

    private Map<UUID, BagTraceIdentityFacts.User> orderUsers(
            Scope scope, List<OrderRow> rows) {
        return resolveUsers(scope, rows.stream()
                .map(row -> new UserRequest(
                        row.userToken(), row.organizationUserId())).toList());
    }

    private Map<UUID, BagTraceIdentityFacts.User> resolveUsers(
            Scope scope, List<UserRequest> requests) {
        if (requests.isEmpty()) {
            return Map.of();
        }
        return identityFacts.resolve(new BagTraceIdentityBatch(
                requests.stream().map(request ->
                        new BagTraceIdentityBatch.Entry(
                                request.token(), scope.tenantId(),
                                scope.organizationId(), request.userId()))
                        .toList())).users();
    }

    private static BagOccupancyEventItem eventItem(
            EventRow row, Map<UUID, PortFact> ports) {
        PortFact port = ports.get(row.portToken());
        if (port == null) {
            throw new IllegalStateException("bag trace port facts missing");
        }
        return new BagOccupancyEventItem(
                row.eventUid(), row.eventType(),
                port.deviceCode(), port.portNo(),
                row.operationUid(), instant(row.occurredAt()),
                row.operationUid() == null
                        ? "BAG_REGISTRATION" : "CLEAN_OPERATION",
                row.operationUid() == null
                        ? row.eventUid().toString()
                        : row.operationUid().toString());
    }

    private static BagCleanRecordItem cleanItem(
            String bagQr,
            CleanRow row,
            Map<UUID, PortFact> ports,
            Map<UUID, BagTraceIdentityFacts.User> users) {
        PortFact port = ports.get(row.portToken());
        BagTraceIdentityFacts.User user = users.get(row.userToken());
        if (port == null || user == null) {
            throw new IllegalStateException("bag trace relation facts missing");
        }
        return new BagCleanRecordItem(
                row.recordNo(), row.operationUid(),
                user.organizationUserUid(), port.deviceCode(),
                port.portNo(),
                bagQr.equals(row.removedBagQr()) ? "REMOVED" : "INSTALLED",
                row.removedBagQr(), row.installedBagQr(),
                instant(row.deviceCompletedAt()),
                grams(row.recalculatedWeightG()),
                grams(row.effectiveWeightG()),
                row.effectiveWeightSource(), row.weightReliability(),
                row.resultKind(),
                row.availablePhotoCount() == 4
                        ? "COMPLETE" : "INCOMPLETE",
                row.recordRemark(), row.version());
    }

    private static BagTraceDeliveryOrderItem orderItem(
            OrderRow row,
            Map<UUID, BagTraceIdentityFacts.User> users) {
        BagTraceIdentityFacts.User user = users.get(row.userToken());
        if (user == null) {
            throw new IllegalStateException("bag trace user facts missing");
        }
        return new BagTraceDeliveryOrderItem(
                row.orderNo(),
                new BagTraceUser(user.organizationUserUid(), user.nickname(),
                        user.maskedPhoneNumber()),
                instant(row.deviceOccurredAt()), instant(row.receivedAt()),
                decimal(row.rawWeight()), yuan(row.rawAmountCent()),
                decimal(row.finalWeight()), yuan(row.finalAmountCent()),
                row.reviewStatus(), row.reason(),
                row.availablePhotoCount() == 4
                        ? "COMPLETE" : "INCOMPLETE");
    }

    private Instant databaseNow() {
        LocalDateTime value = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        if (value == null) {
            throw new IllegalStateException("database time unavailable");
        }
        return instant(value);
    }

    private static EventRow eventRow(ResultSet rs) throws SQLException {
        return new EventRow(
                UUID.fromString(rs.getString("event_uid")),
                rs.getString("event_type"),
                UUID.randomUUID(), rs.getLong("port_id"),
                nullableUuid(rs, "operation_uid"),
                rs.getObject("occurred_at", LocalDateTime.class));
    }

    private static CleanRow cleanRow(ResultSet rs) throws SQLException {
        return new CleanRow(
                rs.getString("clean_record_no"),
                UUID.fromString(rs.getString("operation_uid")),
                UUID.randomUUID(), rs.getLong("cleaner_organization_user_id"),
                UUID.randomUUID(), rs.getLong("port_id"),
                rs.getString("old_bag_code_snapshot"),
                rs.getString("new_bag_code_snapshot"),
                rs.getObject("device_occurred_at", LocalDateTime.class),
                nullableLong(rs, "recalculated_removed_net_weight_g"),
                nullableLong(rs, "effective_removed_net_weight_g"),
                rs.getString("effective_weight_source"),
                rs.getString("recalculated_removed_net_weight_status"),
                rs.getString("record_class"),
                rs.getInt("available_photo_count"),
                rs.getString("record_remark"), rs.getLong("lock_version"));
    }

    private static OrderRow orderRow(ResultSet rs) throws SQLException {
        return new OrderRow(
                rs.getString("delivery_order_no"),
                UUID.randomUUID(), rs.getLong("organization_user_id"),
                rs.getObject("device_occurred_at", LocalDateTime.class),
                rs.getObject("backend_received_at", LocalDateTime.class),
                rs.getBigDecimal("raw_business_weight_kg"),
                nullableLong(rs, "raw_amount_cent"),
                rs.getBigDecimal("final_business_weight_kg"),
                nullableLong(rs, "final_amount_cent"),
                rs.getString("review_status"), rs.getString("reason"),
                rs.getInt("available_photo_count"));
    }

    private static String bag(String value) {
        if (value == null
                || !value.trim().matches("[A-Za-z0-9_-]{8,64}")) {
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

    private static String encodeEvent(String bagQr, EventRow row) {
        return encode("E|" + bagQr + "|" + row.occurredAt()
                + "|" + row.eventUid());
    }

    private static EventCursor eventCursor(String bagQr, String cursor) {
        if (cursor == null || cursor.isBlank()) {
            return null;
        }
        try {
            String[] parts = decode(cursor).split("\\|", -1);
            if (parts.length != 4 || !"E".equals(parts[0])
                    || !bagQr.equals(parts[1])) {
                throw new IllegalArgumentException();
            }
            return new EventCursor(
                    LocalDateTime.parse(parts[2]), UUID.fromString(parts[3]));
        } catch (Exception exception) {
            throw invalidCursor();
        }
    }

    private static String encodeRecord(String bagQr, CleanRow row) {
        return encode("R|" + bagQr + "|" + row.deviceCompletedAt()
                + "|" + row.recordNo());
    }

    private static RecordCursor recordCursor(String bagQr, String cursor) {
        if (cursor == null || cursor.isBlank()) {
            return null;
        }
        try {
            String[] parts = decode(cursor).split("\\|", -1);
            if (parts.length != 4 || !"R".equals(parts[0])
                    || !bagQr.equals(parts[1])) {
                throw new IllegalArgumentException();
            }
            return new RecordCursor(
                    LocalDateTime.parse(parts[2]), parts[3]);
        } catch (Exception exception) {
            throw invalidCursor();
        }
    }

    private static String encodeOrder(String bagQr, OrderRow row) {
        return encode("O|" + bagQr + "|" + row.receivedAt()
                + "|" + row.orderNo());
    }

    private static OrderCursor orderCursor(String bagQr, String cursor) {
        if (cursor == null || cursor.isBlank()) {
            return null;
        }
        try {
            String[] parts = decode(cursor).split("\\|", -1);
            if (parts.length != 4 || !"O".equals(parts[0])
                    || !bagQr.equals(parts[1])) {
                throw new IllegalArgumentException();
            }
            return new OrderCursor(
                    LocalDateTime.parse(parts[2]), parts[3]);
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

    private static Long nullableLong(ResultSet rs, String column)
            throws SQLException {
        Number value = (Number) rs.getObject(column);
        return value == null ? null : value.longValue();
    }

    private static UUID nullableUuid(ResultSet rs, String column)
            throws SQLException {
        String value = rs.getString(column);
        return value == null ? null : UUID.fromString(value);
    }

    private static String grams(Long value) {
        if (value == null) {
            return null;
        }
        BigDecimal kilograms = BigDecimal.valueOf(value, 3)
                .stripTrailingZeros();
        if (kilograms.scale() < 2) {
            kilograms = kilograms.setScale(2);
        }
        return kilograms.toPlainString();
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
    private record BagRow(
            long id,
            LocalDateTime registeredAt,
            String occupancyType,
            Long portId,
            UUID operationUid,
            LocalDateTime acquiredAt,
            LocalDateTime lastRelationChangedAt) { }
    private record PortFact(String deviceCode, int portNo) { }
    private record PortRequest(UUID token, long portId) { }
    private record UserRequest(UUID token, long userId) { }
    private record EventCursor(LocalDateTime occurredAt, UUID eventUid) { }
    private record RecordCursor(LocalDateTime completedAt, String recordNo) { }
    private record OrderCursor(LocalDateTime receivedAt, String orderNo) { }
    private record EventRow(
            UUID eventUid,
            String eventType,
            UUID portToken,
            long portId,
            UUID operationUid,
            LocalDateTime occurredAt) { }
    private record CleanRow(
            String recordNo,
            UUID operationUid,
            UUID userToken,
            long organizationUserId,
            UUID portToken,
            long portId,
            String removedBagQr,
            String installedBagQr,
            LocalDateTime deviceCompletedAt,
            Long recalculatedWeightG,
            Long effectiveWeightG,
            String effectiveWeightSource,
            String weightReliability,
            String resultKind,
            int availablePhotoCount,
            String recordRemark,
            long version) { }
    private record OrderRow(
            String orderNo,
            UUID userToken,
            long organizationUserId,
            LocalDateTime deviceOccurredAt,
            LocalDateTime receivedAt,
            BigDecimal rawWeight,
            Long rawAmountCent,
            BigDecimal finalWeight,
            Long finalAmountCent,
            String reviewStatus,
            String reason,
            int availablePhotoCount) { }
}
