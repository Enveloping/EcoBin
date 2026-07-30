package org.enveloping.ecobin.recycling.application.photo;

import org.enveloping.ecobin.device.api.port.ApplyTrustedPhotoStatusBusinessPort;
import org.enveloping.ecobin.device.api.result.DeliveryCompletionPersistenceFacts;
import org.enveloping.ecobin.device.api.result.DeliveryCompletionResultReference;
import org.enveloping.ecobin.device.api.result.PhotoStatusBusinessResult;
import org.enveloping.ecobin.device.api.result.TrustedPhotoStatusFact;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.LocalDateTime;
import java.util.Arrays;
import java.util.List;
import java.util.Set;

/**
 * Owns the recycling-side lifecycle of independently reported photo terminal
 * facts. A fact may be staged before its delivery or clean record exists.
 */
@Service
public class RecyclingPhotoStatusService
        implements ApplyTrustedPhotoStatusBusinessPort {

    private static final Set<String> DELIVERY_POSITIONS = Set.of(
            "BEFORE_INNER",
            "BEFORE_OUTER",
            "AFTER_INNER",
            "AFTER_OUTER");
    private static final Set<String> CLEAN_POSITIONS = Set.of(
            "FIRST_OPEN_INNER",
            "FIRST_OPEN_OUTER",
            "FINAL_CLOSE_INNER",
            "FINAL_CLOSE_OUTER");

    private final JdbcTemplate jdbc;

    public RecyclingPhotoStatusService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public PhotoStatusBusinessResult apply(
            TrustedPhotoStatusFact fact) {
        requireSupportedWorkSlot(fact);
        List<TerminalFactRow> existing = lockTerminalFact(fact);
        if (!existing.isEmpty()) {
            if (existing.size() == 1
                    && existing.getFirst().matches(fact)) {
                return applied(
                        "NO_ACTION_REQUIRED",
                        fact.workUid() + ":" + fact.position());
            }
            return PhotoStatusBusinessResult.conflict(
                    "PHOTO_TERMINAL_CONFLICT");
        }

        TargetPhoto target = lockTargetPhoto(fact);
        if (target != null && target.terminal()) {
            if (!target.matches(fact)) {
                return PhotoStatusBusinessResult.conflict(
                        "PHOTO_TERMINAL_CONFLICT");
            }
        } else if (target != null) {
            writeTargetPhoto(target, fact);
        }
        insertTerminalFact(fact, target);
        return applied(
                target == null
                        ? "CREATED"
                        : target.terminal()
                        ? "NO_ACTION_REQUIRED"
                        : "UPDATED",
                fact.workUid() + ":" + fact.position());
    }

    /**
     * Links terminal events that arrived before delivery completion. The
     * terminal event is later in the photo lifecycle than the completion
     * snapshot, so it deliberately replaces the snapshot slot state.
     */
    @Transactional(propagation = Propagation.MANDATORY)
    public void mergeStagedDeliveryFacts(
            DeliveryCompletionPersistenceFacts facts,
            long orderId) {
        List<TerminalFactRow> staged = jdbc.query("""
                        SELECT
                            id, edge_event_id, position,
                            status, photo_uid,
                            object_url, sha256, size_bytes,
                            captured_at, missing_reason,
                            delivery_photo_id, clean_photo_id
                        FROM rec_photo_terminal_fact
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND work_type = 'DELIVERY_SESSION'
                          AND work_uid = ?
                          AND linked_at IS NULL
                        FOR UPDATE
                        """,
                RecyclingPhotoStatusService::terminalFactRow,
                facts.tenantId(),
                facts.organizationId(),
                facts.deploymentId(),
                facts.physicalFact().sessionUid().toString());
        for (TerminalFactRow terminal : staged) {
            TargetPhoto target = lockDeliveryPhoto(
                    facts.tenantId(),
                    facts.organizationId(),
                    orderId,
                    terminal.position());
            if (target == null) {
                throw new IllegalStateException(
                        "delivery photo slot disappeared during merge");
            }
            if (target.terminal()) {
                if (!target.matches(terminal)) {
                    throw new IllegalStateException(
                            "delivery completion conflicts with an "
                                    + "accepted photo terminal fact");
                }
            } else {
                writeTargetPhoto(target, terminal);
            }
            linkTerminalFact(
                    terminal.id(),
                    target,
                    facts.backendReceivedAt());
        }
    }

    private TargetPhoto lockTargetPhoto(
            TrustedPhotoStatusFact fact) {
        if ("DELIVERY_SESSION".equals(fact.workType())) {
            List<TargetPhoto> rows = jdbc.query("""
                            SELECT
                                photo.id, photo.status,
                                photo.photo_uid, photo.object_url,
                                photo.sha256, photo.size_bytes,
                                photo.captured_at, photo.missing_reason
                            FROM dev_delivery_session session
                            JOIN rec_delivery_order delivery
                              ON delivery.tenant_id = session.tenant_id
                             AND delivery.organization_id =
                                 session.organization_id
                             AND delivery.delivery_session_id = session.id
                            JOIN rec_delivery_photo photo
                              ON photo.tenant_id = delivery.tenant_id
                             AND photo.organization_id =
                                 delivery.organization_id
                             AND photo.delivery_order_id = delivery.id
                            WHERE session.tenant_id = ?
                              AND session.organization_id = ?
                              AND session.deployment_id = ?
                              AND session.session_uid = ?
                              AND photo.position = ?
                            FOR UPDATE
                            """,
                    (rs, ignored) -> targetPhoto(
                            rs, "DELIVERY_SESSION"),
                    fact.tenantId(),
                    fact.organizationId(),
                    fact.deploymentId(),
                    fact.workUid().toString(),
                    fact.position());
            return exactlyZeroOrOne(rows);
        }
        List<TargetPhoto> rows = jdbc.query("""
                        SELECT
                            photo.id, photo.status,
                            photo.photo_uid, photo.object_url,
                            photo.sha256, photo.size_bytes,
                            photo.captured_at, photo.missing_reason
                        FROM rec_clean_operation operation
                        JOIN rec_clean_photo photo
                          ON photo.tenant_id = operation.tenant_id
                         AND photo.organization_id =
                             operation.organization_id
                         AND photo.clean_operation_id = operation.id
                        WHERE operation.tenant_id = ?
                          AND operation.organization_id = ?
                          AND operation.deployment_id = ?
                          AND operation.operation_uid = ?
                          AND photo.position = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> targetPhoto(
                        rs, "CLEAN_OPERATION"),
                fact.tenantId(),
                fact.organizationId(),
                fact.deploymentId(),
                fact.workUid().toString(),
                fact.position());
        return exactlyZeroOrOne(rows);
    }

    private TargetPhoto lockDeliveryPhoto(
            long tenantId,
            long organizationId,
            long orderId,
            String position) {
        List<TargetPhoto> rows = jdbc.query("""
                        SELECT
                            id, status, photo_uid, object_url,
                            sha256, size_bytes,
                            captured_at, missing_reason
                        FROM rec_delivery_photo
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND delivery_order_id = ?
                          AND position = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> targetPhoto(
                        rs, "DELIVERY_SESSION"),
                tenantId,
                organizationId,
                orderId,
                position);
        return exactlyZeroOrOne(rows);
    }

    private List<TerminalFactRow> lockTerminalFact(
            TrustedPhotoStatusFact fact) {
        return jdbc.query("""
                        SELECT
                            id, edge_event_id, position,
                            status, photo_uid, object_url,
                            sha256, size_bytes,
                            captured_at, missing_reason,
                            delivery_photo_id, clean_photo_id
                        FROM rec_photo_terminal_fact
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND work_type = ?
                          AND work_uid = ?
                          AND position = ?
                        FOR UPDATE
                        """,
                RecyclingPhotoStatusService::terminalFactRow,
                fact.tenantId(),
                fact.organizationId(),
                fact.workType(),
                fact.workUid().toString(),
                fact.position());
    }

    private void insertTerminalFact(
            TrustedPhotoStatusFact fact,
            TargetPhoto target) {
        int inserted = jdbc.update("""
                        INSERT INTO rec_photo_terminal_fact (
                            tenant_id, organization_id, deployment_id,
                            edge_event_id, edge_event_type,
                            work_type, work_uid, position,
                            status, photo_uid, object_url,
                            sha256, size_bytes, captured_at,
                            missing_reason,
                            device_occurred_at, backend_received_at,
                            delivery_photo_id, clean_photo_id, linked_at,
                            created_at, updated_at
                        ) VALUES (
                            ?, ?, ?,
                            ?, 'PHOTO_STATUS_REPORTED',
                            ?, ?, ?,
                            ?, ?, ?,
                            ?, ?, ?,
                            ?,
                            ?, ?,
                            ?, ?, ?,
                            ?, ?
                        )
                        """,
                fact.tenantId(),
                fact.organizationId(),
                fact.deploymentId(),
                fact.edgeEventId(),
                fact.workType(),
                fact.workUid().toString(),
                fact.position(),
                fact.status(),
                nullableUuid(fact),
                fact.objectUrl(),
                fact.sha256(),
                fact.sizeBytes(),
                fact.capturedAt(),
                fact.missingReason(),
                fact.deviceOccurredAt(),
                fact.backendReceivedAt(),
                target != null
                                && "DELIVERY_SESSION".equals(
                                target.workType())
                        ? target.id()
                        : null,
                target != null
                                && "CLEAN_OPERATION".equals(
                                target.workType())
                        ? target.id()
                        : null,
                target == null ? null : fact.backendReceivedAt(),
                fact.backendReceivedAt(),
                fact.backendReceivedAt());
        requireSingle(inserted, "insert photo terminal fact");
    }

    private void writeTargetPhoto(
            TargetPhoto target,
            TrustedPhotoStatusFact fact) {
        updateTargetPhoto(
                target,
                fact.status(),
                nullableUuid(fact),
                fact.objectUrl(),
                fact.sha256(),
                fact.sizeBytes(),
                fact.capturedAt(),
                fact.missingReason(),
                fact.backendReceivedAt());
    }

    private void writeTargetPhoto(
            TargetPhoto target,
            TerminalFactRow fact) {
        updateTargetPhoto(
                target,
                fact.status(),
                fact.photoUid(),
                fact.objectUrl(),
                fact.sha256(),
                fact.sizeBytes(),
                fact.capturedAt(),
                fact.missingReason(),
                databaseNow());
    }

    private void updateTargetPhoto(
            TargetPhoto target,
            String status,
            String photoUid,
            String objectUrl,
            byte[] sha256,
            Long sizeBytes,
            LocalDateTime capturedAt,
            String missingReason,
            LocalDateTime now) {
        String table = "DELIVERY_SESSION".equals(target.workType())
                ? "rec_delivery_photo"
                : "rec_clean_photo";
        int updated = jdbc.update("""
                        UPDATE %s
                        SET photo_uid = ?,
                            status = ?,
                            object_url = ?,
                            sha256 = ?,
                            size_bytes = ?,
                            captured_at = ?,
                            linked_at = ?,
                            missing_reason = ?,
                            updated_at = ?
                        WHERE id = ?
                        """.formatted(table),
                photoUid,
                status,
                objectUrl,
                sha256,
                sizeBytes,
                capturedAt,
                now,
                missingReason,
                now,
                target.id());
        requireSingle(updated, "merge photo terminal state");
    }

    private void linkTerminalFact(
            long terminalFactId,
            TargetPhoto target,
            LocalDateTime now) {
        int updated = jdbc.update("""
                        UPDATE rec_photo_terminal_fact
                        SET delivery_photo_id = ?,
                            clean_photo_id = ?,
                            linked_at = ?,
                            updated_at = ?
                        WHERE id = ?
                          AND linked_at IS NULL
                        """,
                "DELIVERY_SESSION".equals(target.workType())
                        ? target.id()
                        : null,
                "CLEAN_OPERATION".equals(target.workType())
                        ? target.id()
                        : null,
                now,
                now,
                terminalFactId);
        requireSingle(updated, "link staged photo terminal fact");
    }

    private static PhotoStatusBusinessResult applied(
            String effectKind,
            String key) {
        return PhotoStatusBusinessResult.applied(
                effectKind,
                List.of(new DeliveryCompletionResultReference(
                        "PHOTO_SLOT",
                        key)));
    }

    private static void requireSupportedWorkSlot(
            TrustedPhotoStatusFact fact) {
        boolean supported =
                "DELIVERY_SESSION".equals(fact.workType())
                        && DELIVERY_POSITIONS.contains(fact.position())
                || "CLEAN_OPERATION".equals(fact.workType())
                        && CLEAN_POSITIONS.contains(fact.position());
        if (!supported) {
            throw new IllegalArgumentException(
                    "photo position does not belong to its work type");
        }
    }

    private static TargetPhoto exactlyZeroOrOne(
            List<TargetPhoto> rows) {
        if (rows.size() > 1) {
            throw new IllegalStateException(
                    "photo work slot maps to multiple rows");
        }
        return rows.isEmpty() ? null : rows.getFirst();
    }

    private static TargetPhoto targetPhoto(
            ResultSet rs,
            String workType) throws SQLException {
        return new TargetPhoto(
                rs.getLong("id"),
                workType,
                rs.getString("status"),
                rs.getString("photo_uid"),
                rs.getString("object_url"),
                rs.getBytes("sha256"),
                nullableLong(rs, "size_bytes"),
                rs.getObject(
                        "captured_at", LocalDateTime.class),
                rs.getString("missing_reason"));
    }

    private static TerminalFactRow terminalFactRow(
            ResultSet rs,
            int ignored) throws SQLException {
        return new TerminalFactRow(
                rs.getLong("id"),
                rs.getLong("edge_event_id"),
                rs.getString("position"),
                rs.getString("status"),
                rs.getString("photo_uid"),
                rs.getString("object_url"),
                rs.getBytes("sha256"),
                nullableLong(rs, "size_bytes"),
                rs.getObject(
                        "captured_at", LocalDateTime.class),
                rs.getString("missing_reason"),
                nullableLong(rs, "delivery_photo_id"),
                nullableLong(rs, "clean_photo_id"));
    }

    private static Long nullableLong(
            ResultSet resultSet,
            String column) throws SQLException {
        long value = resultSet.getLong(column);
        return resultSet.wasNull() ? null : value;
    }

    private static String nullableUuid(
            TrustedPhotoStatusFact fact) {
        return fact.photoUid() == null
                ? null
                : fact.photoUid().toString();
    }

    private LocalDateTime databaseNow() {
        return jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
    }

    private static void requireSingle(
            int updated,
            String operation) {
        if (updated != 1) {
            throw new IllegalStateException(
                    operation + " updated " + updated + " rows");
        }
    }

    private record TargetPhoto(
            long id,
            String workType,
            String status,
            String photoUid,
            String objectUrl,
            byte[] sha256,
            Long sizeBytes,
            LocalDateTime capturedAt,
            String missingReason) {

        private boolean terminal() {
            return Set.of(
                    "AVAILABLE",
                    "PERMANENTLY_MISSING").contains(status);
        }

        private boolean matches(TrustedPhotoStatusFact fact) {
            return status.equals(fact.status())
                    && java.util.Objects.equals(
                    photoUid, nullableUuid(fact))
                    && java.util.Objects.equals(
                    objectUrl, fact.objectUrl())
                    && Arrays.equals(sha256, fact.sha256())
                    && java.util.Objects.equals(
                    sizeBytes, fact.sizeBytes())
                    && java.util.Objects.equals(
                    capturedAt, fact.capturedAt())
                    && java.util.Objects.equals(
                    missingReason, fact.missingReason());
        }

        private boolean matches(TerminalFactRow fact) {
            return status.equals(fact.status())
                    && java.util.Objects.equals(
                    photoUid, fact.photoUid())
                    && java.util.Objects.equals(
                    objectUrl, fact.objectUrl())
                    && Arrays.equals(sha256, fact.sha256())
                    && java.util.Objects.equals(
                    sizeBytes, fact.sizeBytes())
                    && java.util.Objects.equals(
                    capturedAt, fact.capturedAt())
                    && java.util.Objects.equals(
                    missingReason, fact.missingReason());
        }
    }

    private record TerminalFactRow(
            long id,
            long edgeEventId,
            String position,
            String status,
            String photoUid,
            String objectUrl,
            byte[] sha256,
            Long sizeBytes,
            LocalDateTime capturedAt,
            String missingReason,
            Long deliveryPhotoId,
            Long cleanPhotoId) {

        private boolean matches(
                TrustedPhotoStatusFact fact) {
            return status.equals(fact.status())
                    && java.util.Objects.equals(
                    photoUid, nullableUuid(fact))
                    && java.util.Objects.equals(
                    objectUrl, fact.objectUrl())
                    && Arrays.equals(sha256, fact.sha256())
                    && java.util.Objects.equals(
                    sizeBytes, fact.sizeBytes())
                    && java.util.Objects.equals(
                    capturedAt, fact.capturedAt())
                    && java.util.Objects.equals(
                    missingReason, fact.missingReason());
        }
    }
}
