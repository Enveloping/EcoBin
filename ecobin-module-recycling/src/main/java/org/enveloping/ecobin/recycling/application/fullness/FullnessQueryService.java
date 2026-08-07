package org.enveloping.ecobin.recycling.application.fullness;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.ManagementScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.query.ManagementScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.query.ManagementScopeAuthorizationQuery.Channel;
import org.enveloping.ecobin.identity.api.result.AuthorizedManagementScope;
import org.enveloping.ecobin.device.api.port.RecyclingDevicePortLookupPort;
import org.enveloping.ecobin.device.api.port.RecyclingDeviceRelationQueryPort;
import org.enveloping.ecobin.device.api.result.ResolvedRecyclingDevicePort;
import org.enveloping.ecobin.device.api.result.RecyclingDeviceRelationFacts;
import org.enveloping.ecobin.recycling.application.devicefacts.RecyclingDeviceRelationBatch;
import org.enveloping.ecobin.recycling.application.devicefacts.RecyclingDeviceRelationBatch.Entry;
import org.enveloping.ecobin.recycling.application.devicefacts.RecyclingDeviceRelationBatch.Kind;
import org.enveloping.ecobin.recycling.web.v1.FullnessModels.FullnessMeasurementSummary;
import org.enveloping.ecobin.recycling.web.v1.FullnessModels.FullnessStateChangeItem;
import org.enveloping.ecobin.recycling.web.v1.FullnessModels.FullnessStateChangePage;
import org.enveloping.ecobin.recycling.web.v1.FullnessModels.PortCapacityView;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
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
import java.util.UUID;

@Service
public class FullnessQueryService {

    private static final int DEFAULT_LIMIT = 20;
    private static final int MAX_LIMIT = 100;

    private final JdbcTemplate jdbc;
    private final ManagementScopeAuthorizationPort authorization;
    private final RecyclingDevicePortLookupPort ports;
    private final RecyclingDeviceRelationQueryPort deviceFacts;

    public FullnessQueryService(
            JdbcTemplate jdbc,
            ManagementScopeAuthorizationPort authorization,
            RecyclingDevicePortLookupPort ports,
            RecyclingDeviceRelationQueryPort deviceFacts) {
        this.jdbc = jdbc;
        this.authorization = authorization;
        this.ports = ports;
        this.deviceFacts = deviceFacts;
    }

    @Transactional(readOnly = true)
    public PortCapacityView webCapacity(
            boolean platform,
            String tenantCode,
            String organizationCode,
            String deviceCode,
            int portNo) {
        AuthorizedManagementScope scope = webScope(
                platform, tenantCode, organizationCode, "device.read");
        return capacity(ports.requirePort(
                scope.persistenceRef(), deviceCode, portNo));
    }

    @Transactional(readOnly = true)
    public FullnessStateChangePage webHistory(
            boolean platform,
            String tenantCode,
            String organizationCode,
            String deviceCode,
            int portNo,
            String cursor,
            Integer requestedLimit) {
        AuthorizedManagementScope scope = webScope(
                platform, tenantCode, organizationCode, "device.read");
        ResolvedRecyclingDevicePort port = ports.requirePort(
                scope.persistenceRef(), deviceCode, portNo);
        int limit = limit(requestedLimit);
        String cursorScope = cursorScope(
                scope.tenantCode(), scope.organizations().getFirst().code(),
                port.deviceCode(), port.portNo());
        HistoryCursor anchor = cursor(cursorScope, cursor);
        return port.persistenceRef().consumeOnce(
                (tenantId, organizationId, portId) -> historyOwned(
                        tenantId, organizationId, portId,
                        cursorScope, anchor, limit));
    }

    private FullnessStateChangePage historyOwned(
            long tenantId,
            long organizationId,
            long portId,
            String cursorScope,
            HistoryCursor anchor,
            int limit) {
        List<Object> args = new ArrayList<>(List.of(
                tenantId, organizationId, portId));
        String anchorSql = "";
        if (anchor != null) {
            anchorSql = " AND (state_change.created_at < ?"
                    + " OR (state_change.created_at = ?"
                    + " AND state_change.id < ?))";
            args.add(anchor.createdAt());
            args.add(anchor.createdAt());
            args.add(anchor.id());
        }
        args.add(limit + 1);
        List<StateRow> rows = jdbc.query(stateSql() + anchorSql + """
                        ORDER BY state_change.created_at DESC,
                                 state_change.id DESC
                        LIMIT ?
                        """,
                (rs, ignored) -> stateRow(rs), args.toArray());
        boolean more = rows.size() > limit;
        List<StateRow> visible = more ? rows.subList(0, limit) : rows;
        var facts = stateFacts(visible, tenantId, organizationId);
        return new FullnessStateChangePage(
                visible.stream().map(row -> item(row, facts)).toList(),
                more ? encode(cursorScope, visible.getLast()) : null,
                databaseNow());
    }

    @Transactional(readOnly = true)
    public FullnessStateChangeItem webStateChange(
            boolean platform,
            String tenantCode,
            String organizationCode,
            String deviceCode,
            int portNo,
            UUID stateChangeUid) {
        AuthorizedManagementScope scope = webScope(
                platform, tenantCode, organizationCode, "device.read");
        ResolvedRecyclingDevicePort port = ports.requirePort(
                scope.persistenceRef(), deviceCode, portNo);
        if (stateChangeUid == null) {
            throw notFound();
        }
        return port.persistenceRef().consumeOnce(
                (tenantId, organizationId, portId) -> {
                    StateRow row = jdbc.query(stateSql()
                                    + " AND state_change.state_change_uid = ?",
                            (rs, ignored) -> stateRow(rs),
                            tenantId, organizationId, portId,
                            stateChangeUid.toString()).stream().findFirst()
                            .orElseThrow(FullnessQueryService::notFound);
                    return item(row, stateFacts(
                            List.of(row), tenantId, organizationId));
                });
    }

    @Transactional(readOnly = true)
    public PortCapacityView staffCapacity(
            String deviceCode, int portNo) {
        var authorized = authorization.authorize(
                new ManagementScopeAuthorizationQuery(
                        Channel.MINIAPP_STAFF,
                        false, null, null, "device.read"));
        return capacity(ports.requirePort(
                authorized.persistenceRef(), deviceCode, portNo));
    }

    private AuthorizedManagementScope webScope(
            boolean platform,
            String tenantCode,
            String organizationCode,
            String capability) {
        return authorization.authorize(
                new ManagementScopeAuthorizationQuery(
                        Channel.WEB,
                        platform,
                        tenantCode,
                        organizationCode,
                        capability));
    }

    private PortCapacityView capacity(
            ResolvedRecyclingDevicePort port) {
        Instant asOf = databaseNow();
        return port.persistenceRef().consumeOnce(
                (tenantId, organizationId, portId) -> jdbc.query("""
                        SELECT
                               bag.bag_code,
                               capacity.baseline_state,
                               capacity.current_baseline_weight_g,
                               capacity.latest_stable_total_weight_g,
                               capacity.raw_net_weight_g,
                               capacity.displayed_fullness_percent,
                               capacity.detection_gate,
                               capacity.confirmed_fullness_state,
                               state_change.state_change_uid,
                               capacity.last_fullness_reported_at,
                               capacity.lock_version
                        FROM rec_port_capacity_state capacity
                        LEFT JOIN rec_bag bag
                          ON bag.id = capacity.current_bag_id
                        LEFT JOIN rec_fullness_state_change state_change
                          ON state_change.id =
                             capacity.current_fullness_state_change_id
                        WHERE capacity.tenant_id = ?
                          AND capacity.organization_id = ?
                          AND capacity.port_id = ?
                        """,
                (rs, ignored) -> new PortCapacityView(
                        port.deviceCode(),
                        port.portNo(),
                        rs.getString("bag_code"),
                        value(rs, "baseline_state", "UNINITIALIZED"),
                        kg((Long) rs.getObject(
                                "current_baseline_weight_g")),
                        kg((Long) rs.getObject(
                                "latest_stable_total_weight_g")),
                        kg((Long) rs.getObject("raw_net_weight_g")),
                        decimal(rs.getBigDecimal(
                                "displayed_fullness_percent")),
                        value(rs, "detection_gate", "UNKNOWN"),
                        value(rs, "confirmed_fullness_state", "UNKNOWN"),
                        rs.getString("state_change_uid") == null
                                ? "NO_DEVICE_REPORT"
                                : "DEVICE_REPORTED",
                        uuid(rs.getString("state_change_uid")),
                        instant(rs.getObject(
                                "last_fullness_reported_at",
                                LocalDateTime.class)),
                        rs.getLong("lock_version"),
                        asOf),
                tenantId, organizationId, portId)
                .stream().findFirst().orElseGet(() ->
                        new PortCapacityView(
                                port.deviceCode(), port.portNo(), null,
                                "UNINITIALIZED", null, null, null, null,
                                "UNKNOWN", "UNKNOWN", "NO_DEVICE_REPORT",
                                null, null, 0, asOf)));
    }

    private static String stateSql() {
        return """
                SELECT state_change.id,
                       state_change.state_change_uid,
                       state_change.reported_state,
                       state_change.disposition,
                       bag.bag_code,
                       state_change.source_work_type,
                       state_change.source_work_uid,
                       state_change.edge_event_sequence,
                       state_change.device_state_fact_id,
                       state_change.device_occurred_at,
                       state_change.backend_received_at,
                       state_change.created_at
                FROM rec_fullness_state_change state_change
                JOIN rec_bag bag ON bag.id = state_change.bag_id
                WHERE state_change.tenant_id = ?
                  AND state_change.organization_id = ?
                  AND state_change.port_id = ?
                """;
    }

    private FullnessStateChangeItem item(
            StateRow row, RecyclingDeviceRelationFacts facts) {
        RecyclingDeviceRelationFacts.FullnessStateFact fact =
                facts.fullnessStateFacts().get(row.factToken());
        if (fact == null) {
            throw new IllegalStateException("fullness state fact missing");
        }
        return new FullnessStateChangeItem(
                row.uid(),
                row.state(),
                row.disposition(),
                row.bag(),
                row.sourceType(),
                row.sourceUid(),
                row.sequence(),
                new FullnessMeasurementSummary(
                        fact.fullnessMode(), fact.sensorKind(),
                        fact.sensorValue(), kg(fact.totalWeightG()),
                        kg(fact.baselineWeightG()),
                        kg(fact.configuredFullWeightG()),
                        hundredths(fact.fullnessPercentHundredths()),
                        fact.sampleCount(), fact.measurementElapsedMs(),
                        fact.confirmationBasis()),
                instant(row.deviceOccurredAt()),
                instant(row.backendReceivedAt()));
    }

    private static StateRow stateRow(ResultSet rs) throws SQLException {
        return new StateRow(
                rs.getLong("id"),
                UUID.fromString(rs.getString("state_change_uid")),
                rs.getString("reported_state"),
                rs.getString("disposition"),
                rs.getString("bag_code"),
                rs.getString("source_work_type"),
                UUID.fromString(rs.getString("source_work_uid")),
                rs.getLong("edge_event_sequence"),
                UUID.randomUUID(),
                rs.getLong("device_state_fact_id"),
                rs.getObject("device_occurred_at", LocalDateTime.class),
                rs.getObject("backend_received_at", LocalDateTime.class),
                rs.getObject("created_at", LocalDateTime.class));
    }

    private RecyclingDeviceRelationFacts stateFacts(
            List<StateRow> rows,
            long tenantId,
            long organizationId) {
        return deviceFacts.resolve(new RecyclingDeviceRelationBatch(
                rows.stream().map(row -> new Entry(
                        row.factToken(), Kind.FULLNESS_STATE_FACT,
                        tenantId, organizationId, row.factId())).toList()));
    }

    private Instant databaseNow() {
        LocalDateTime result = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        if (result == null) {
            throw new IllegalStateException("database time unavailable");
        }
        return instant(result);
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

    private static String encode(String cursorScope, StateRow row) {
        String value = cursorScope + "|" + row.createdAt() + "|" + row.id();
        return Base64.getUrlEncoder().withoutPadding().encodeToString(
                value.getBytes(StandardCharsets.UTF_8));
    }

    private static HistoryCursor cursor(
            String cursorScope, String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        try {
            String decoded = new String(Base64.getUrlDecoder().decode(value),
                    StandardCharsets.UTF_8);
            String[] parts = decoded.split("\\|", -1);
            if (parts.length != 3 || !cursorScope.equals(parts[0])) {
                throw new IllegalArgumentException();
            }
            return new HistoryCursor(
                    LocalDateTime.parse(parts[1]),
                    Long.parseLong(parts[2]));
        } catch (Exception exception) {
            throw new TargetApiException(
                    400, "COMMON.INVALID_CURSOR", "分页游标无效或已过期");
        }
    }

    private static String cursorScope(
            String tenantCode, String organizationCode,
            String deviceCode, int portNo) {
        return tenantCode + ":" + organizationCode
                + ":" + deviceCode + ":" + portNo;
    }

    private static String value(
            ResultSet rs, String column, String fallback)
            throws SQLException {
        String value = rs.getString(column);
        return value == null ? fallback : value;
    }

    private static String kg(Long grams) {
        return grams == null ? null
                : BigDecimal.valueOf(grams, 3)
                .setScale(2, java.math.RoundingMode.HALF_UP)
                .toPlainString();
    }

    private static String decimal(BigDecimal value) {
        return value == null ? null : value.setScale(2).toPlainString();
    }

    private static String hundredths(Long value) {
        return value == null ? null
                : BigDecimal.valueOf(value, 2).setScale(2).toPlainString();
    }

    private static UUID uuid(String value) {
        return value == null ? null : UUID.fromString(value);
    }

    private static Instant instant(LocalDateTime value) {
        return value == null ? null : value.toInstant(ZoneOffset.UTC);
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404, "RESOURCE.NOT_FOUND", "资源不存在");
    }

    private record HistoryCursor(LocalDateTime createdAt, long id) { }
    private record StateRow(
            long id,
            UUID uid,
            String state,
            String disposition,
            String bag,
            String sourceType,
            UUID sourceUid,
            long sequence,
            UUID factToken,
            long factId,
            LocalDateTime deviceOccurredAt,
            LocalDateTime backendReceivedAt,
            LocalDateTime createdAt) { }
}
