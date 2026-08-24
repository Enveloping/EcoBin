package org.enveloping.ecobin.recycling.application.fullness;

import org.enveloping.ecobin.device.api.port.CompleteFullnessStateChangeDeviceParticipationPort;
import org.enveloping.ecobin.device.api.result.DeliveryCompletionResultReference;
import org.enveloping.ecobin.device.api.result.FullnessStateChangeBusinessResult;
import org.enveloping.ecobin.device.api.result.FullnessStateChangePersistenceFacts;
import org.enveloping.ecobin.device.api.result.FullnessStateChangePhysicalFact;
import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;
import org.enveloping.ecobin.framework.reliability.UntrustedInboxSourceException;
import org.enveloping.ecobin.recycling.api.port.ApplyFullnessStateChangedUseCase;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.LocalDateTime;
import java.util.List;

/** Applies an edge-reported state transition only to its current bag. */
@Service
public class ApplyFullnessStateChangedService
        implements ApplyFullnessStateChangedUseCase {

    private final CompleteFullnessStateChangeDeviceParticipationPort device;
    private final JdbcTemplate jdbc;

    public ApplyFullnessStateChangedService(
            CompleteFullnessStateChangeDeviceParticipationPort device,
            JdbcTemplate jdbc) {
        this.device = device;
        this.jdbc = jdbc;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public TrustedDeviceEventApplyResult apply(
            TrustedDeviceInboxEvent event) {
        return device.complete(
                event,
                reference -> reference.useOnce(
                        this::persistBusinessFacts));
    }

    private FullnessStateChangeBusinessResult persistBusinessFacts(
            FullnessStateChangePersistenceFacts facts) {
        FullnessStateChangePhysicalFact physical = facts.physicalFact();
        Source source = requireSourceWork(facts);
        CurrentBag currentBag = lockCurrentBag(facts);
        Capacity capacity = lockOrInitializeCapacity(
                facts, currentBag);

        String disposition = disposition(
                currentBag.id(),
                currentBag.uid(),
                source.bagId(),
                physical.bagUid().toString(),
                capacity.currentBagId(),
                capacity.lastEdgeSequence(),
                physical.edgeEventSequence(),
                capacity.fullnessState(),
                physical.state());

        long changeId = insertHistory(
                facts,
                source,
                currentBag,
                disposition);
        if (!"STALE_BAG".equals(disposition)
                && !"STALE_SEQUENCE".equals(disposition)) {
            projectCurrentState(
                    facts,
                    currentBag,
                    capacity,
                    changeId);
        }
        return new FullnessStateChangeBusinessResult(
                List.of(new DeliveryCompletionResultReference(
                        "PORT_FULLNESS_STATE",
                        physical.stateChangeUid().toString())));
    }

    private Source requireSourceWork(
            FullnessStateChangePersistenceFacts facts) {
        FullnessStateChangePhysicalFact physical = facts.physicalFact();
        if ("DELIVERY_SESSION".equals(
                physical.sourceWorkType())) {
            if (facts.sourceDeliverySessionId() == null) {
                throw untrusted(
                        "fullness delivery source identity is missing");
            }
            List<Source> rows = jdbc.query("""
                            SELECT id, bag_id
                            FROM rec_delivery_order
                            WHERE tenant_id = ?
                              AND organization_id = ?
                              AND asset_id = ?
                              AND port_id = ?
                              AND delivery_session_id = ?
                            FOR UPDATE
                            """,
                    (rs, ignored) -> new Source(
                            "DELIVERY_SESSION",
                            rs.getLong("id"),
                            null,
                            rs.getLong("bag_id")),
                    facts.tenantId(),
                    facts.organizationId(),
                    facts.assetId(),
                    facts.portId(),
                    facts.sourceDeliverySessionId());
            if (rows.size() != 1) {
                throw new IllegalStateException(
                        "fullness delivery order is not committed yet");
            }
            return rows.getFirst();
        }
        List<Source> rows = jdbc.query("""
                        SELECT operation.completion_record_id,
                               operation.new_bag_id
                        FROM rec_clean_operation operation
                        WHERE operation.tenant_id = ?
                          AND operation.organization_id = ?
                          AND operation.asset_id = ?
                          AND operation.port_id = ?
                          AND operation.operation_uid = ?
                          AND operation.status = 'COMPLETED'
                          AND operation.completion_record_id IS NOT NULL
                        FOR UPDATE
                        """,
                (rs, ignored) -> new Source(
                        "CLEAN_OPERATION",
                        null,
                        rs.getLong("completion_record_id"),
                        rs.getLong("new_bag_id")),
                facts.tenantId(),
                facts.organizationId(),
                facts.assetId(),
                facts.portId(),
                physical.sourceWorkUid().toString());
        if (rows.size() != 1) {
            throw new IllegalStateException(
                    "fullness clean record is not committed yet");
        }
        return rows.getFirst();
    }

    private CurrentBag lockCurrentBag(
            FullnessStateChangePersistenceFacts facts) {
        List<CurrentBag> rows = jdbc.query("""
                        SELECT bag.id, bag.bag_uid
                        FROM rec_bag_current_occupancy occupancy
                        JOIN rec_bag bag
                          ON bag.tenant_id = occupancy.tenant_id
                         AND bag.organization_id =
                             occupancy.organization_id
                         AND bag.id = occupancy.bag_id
                        WHERE occupancy.tenant_id = ?
                          AND occupancy.organization_id = ?
                          AND occupancy.port_id = ?
                          AND occupancy.occupancy_type = 'PORT_BOUND'
                        FOR UPDATE OF occupancy
                        """,
                (rs, ignored) -> new CurrentBag(
                        rs.getLong("id"),
                        rs.getString("bag_uid")),
                facts.tenantId(),
                facts.organizationId(),
                facts.portId());
        if (rows.size() != 1) {
            throw new IllegalStateException(
                    "fullness current bag is not available yet");
        }
        return rows.getFirst();
    }

    private Capacity lockOrInitializeCapacity(
            FullnessStateChangePersistenceFacts facts,
            CurrentBag currentBag) {
        Baseline baseline = latestBaseline(facts, currentBag.id());
        jdbc.update("""
                        INSERT INTO rec_port_capacity_state (
                            port_id, tenant_id, organization_id,
                            asset_id,
                            baseline_state, current_baseline_id,
                            current_baseline_weight_g,
                            latest_stable_total_weight_g,
                            raw_net_weight_g,
                            displayed_fullness_percent,
                            detection_gate, current_detection_id,
                            current_rule_fingerprint,
                            confirmed_fullness_state,
                            last_detection_id,
                            current_fullness_event_id,
                            current_bag_id,
                            current_fullness_state_change_id,
                            last_fullness_edge_event_id,
                            last_fullness_edge_event_sequence,
                            last_fullness_reported_at,
                            lock_version, updated_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?,
                            NULL, NULL, NULL,
                            'READY', NULL, NULL, 'NOT_FULL',
                            NULL, NULL, ?, NULL, NULL, NULL, NULL,
                            0, ?
                        )
                        ON DUPLICATE KEY UPDATE
                            updated_at = rec_port_capacity_state.updated_at
                        """,
                facts.portId(),
                facts.tenantId(),
                facts.organizationId(),
                facts.assetId(),
                baseline.id() == null ? "UNINITIALIZED" : "VALID",
                baseline.id(),
                baseline.weightGrams(),
                currentBag.id(),
                facts.backendReceivedAt());
        List<Capacity> rows = jdbc.query("""
                        SELECT current_bag_id,
                               confirmed_fullness_state,
                               last_fullness_edge_event_sequence,
                               lock_version
                        FROM rec_port_capacity_state
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND port_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> capacity(rs),
                facts.tenantId(),
                facts.organizationId(),
                facts.assetId(),
                facts.portId());
        if (rows.size() != 1) {
            throw new IllegalStateException(
                    "fullness capacity state is unavailable");
        }
        return rows.getFirst();
    }

    private Baseline latestBaseline(
            FullnessStateChangePersistenceFacts facts,
            long bagId) {
        return jdbc.query("""
                        SELECT id, baseline_weight_g
                        FROM rec_port_weight_baseline
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND port_id = ?
                          AND bag_id = ?
                        ORDER BY version_no DESC
                        LIMIT 1
                        """,
                (rs, ignored) -> new Baseline(
                        rs.getLong("id"),
                        rs.getLong("baseline_weight_g")),
                facts.tenantId(),
                facts.organizationId(),
                facts.portId(),
                bagId).stream().findFirst()
                .orElse(new Baseline(null, null));
    }

    private long insertHistory(
            FullnessStateChangePersistenceFacts facts,
            Source source,
            CurrentBag currentBag,
            String disposition) {
        FullnessStateChangePhysicalFact physical = facts.physicalFact();
        requireSingle(jdbc.update("""
                        INSERT INTO rec_fullness_state_change (
                            state_change_uid,
                            tenant_id, organization_id,
                            asset_id, port_id,
                            bag_id, reported_bag_uid,
                            device_state_fact_id, edge_event_id,
                            edge_event_sequence,
                            source_work_type, source_work_uid,
                            delivery_order_id, clean_record_id,
                            reported_state, disposition,
                            device_occurred_at,
                            backend_received_at, created_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                physical.stateChangeUid().toString(),
                facts.tenantId(),
                facts.organizationId(),
                facts.assetId(),
                facts.portId(),
                source.bagId(),
                physical.bagUid().toString(),
                facts.stateFactId(),
                facts.edgeEventId(),
                physical.edgeEventSequence(),
                source.type(),
                physical.sourceWorkUid().toString(),
                source.deliveryOrderId(),
                source.cleanRecordId(),
                physical.state(),
                disposition,
                physical.deviceOccurredAt() == null
                        ? null
                        : LocalDateTime.ofInstant(
                                physical.deviceOccurredAt(),
                                java.time.ZoneOffset.UTC),
                facts.backendReceivedAt(),
                facts.backendReceivedAt()),
                "insert fullness state history");
        Long id = jdbc.queryForObject("""
                        SELECT id FROM rec_fullness_state_change
                        WHERE state_change_uid = ?
                        """,
                Long.class,
                physical.stateChangeUid().toString());
        if (id == null) {
            throw new IllegalStateException(
                    "fullness state history id is missing");
        }
        return id;
    }

    private void projectCurrentState(
            FullnessStateChangePersistenceFacts facts,
            CurrentBag currentBag,
            Capacity capacity,
            long changeId) {
        FullnessStateChangePhysicalFact physical = facts.physicalFact();
        Long baseline = physical.baselineWeightGrams();
        Long total = physical.totalWeightMeasurement()
                .reportedWeightGrams();
        Long rawNet = baseline == null || total == null
                ? null : Math.subtractExact(total, baseline);
        BigDecimal percent = physical.fullnessPercentHundredths() == null
                ? null
                : BigDecimal.valueOf(
                        physical.fullnessPercentHundredths())
                .divide(BigDecimal.valueOf(100),
                        2, RoundingMode.UNNECESSARY);
        requireSingle(jdbc.update("""
                        UPDATE rec_port_capacity_state
                        SET latest_stable_total_weight_g = ?,
                            raw_net_weight_g = ?,
                            displayed_fullness_percent = ?,
                            detection_gate = 'READY',
                            current_detection_id = NULL,
                            confirmed_fullness_state = ?,
                            current_fullness_event_id = NULL,
                            current_bag_id = ?,
                            current_fullness_state_change_id = ?,
                            last_fullness_edge_event_id = ?,
                            last_fullness_edge_event_sequence = ?,
                            last_fullness_reported_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND port_id = ?
                          AND lock_version = ?
                        """,
                total,
                rawNet,
                percent,
                physical.state(),
                currentBag.id(),
                changeId,
                facts.edgeEventId(),
                physical.edgeEventSequence(),
                facts.backendReceivedAt(),
                facts.backendReceivedAt(),
                facts.tenantId(),
                facts.organizationId(),
                facts.assetId(),
                facts.portId(),
                capacity.lockVersion()),
                "project current-bag fullness state");
    }

    private static Capacity capacity(ResultSet rs)
            throws SQLException {
        long bag = rs.getLong("current_bag_id");
        Long currentBagId = rs.wasNull() ? null : bag;
        long sequence = rs.getLong(
                "last_fullness_edge_event_sequence");
        Long lastSequence = rs.wasNull() ? null : sequence;
        return new Capacity(
                currentBagId,
                rs.getString("confirmed_fullness_state"),
                lastSequence,
                rs.getLong("lock_version"));
    }

    static String disposition(
            long currentBagId,
            String currentBagUid,
            long sourceBagId,
            String reportedBagUid,
            Long capacityBagId,
            Long lastEdgeSequence,
            long reportedEdgeSequence,
            String currentState,
            String reportedState) {
        if (currentBagId != sourceBagId
                || !currentBagUid.equals(reportedBagUid)) {
            return "STALE_BAG";
        }
        boolean sameCapacityBag = Long.valueOf(currentBagId)
                .equals(capacityBagId);
        if (sameCapacityBag
                && lastEdgeSequence != null
                && reportedEdgeSequence <= lastEdgeSequence) {
            return "STALE_SEQUENCE";
        }
        if (sameCapacityBag && reportedState.equals(currentState)) {
            return "NO_STATE_CHANGE";
        }
        return "APPLIED";
    }

    private static void requireSingle(int affected, String operation) {
        if (affected != 1) {
            throw new IllegalStateException(
                    operation + " affected " + affected + " rows");
        }
    }

    private static UntrustedInboxSourceException untrusted(String detail) {
        return new UntrustedInboxSourceException(detail);
    }

    private record Source(
            String type,
            Long deliveryOrderId,
            Long cleanRecordId,
            long bagId) {
    }

    private record CurrentBag(long id, String uid) {
    }

    private record Capacity(
            Long currentBagId,
            String fullnessState,
            Long lastEdgeSequence,
            long lockVersion) {
    }

    private record Baseline(Long id, Long weightGrams) {
    }
}
