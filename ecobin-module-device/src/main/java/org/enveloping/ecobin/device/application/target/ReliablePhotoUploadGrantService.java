package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.port.PhotoGrantWorkVerificationPort;
import org.enveloping.ecobin.device.api.result.PhotoGrantWorkVerification;
import org.enveloping.ecobin.device.api.result.TrustedPhotoGrantWork;
import org.enveloping.ecobin.framework.reliability.DeviceDeploymentTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceControlTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceControlTaskRegistrationPort;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * Persists an authenticated edge request for a fresh COS grant and creates a
 * credential-free device control task. Real temporary credentials are added
 * only by the outbound adapter immediately before each service call.
 */
@Service
public class ReliablePhotoUploadGrantService {

    static final String TASK_TYPE = "PROVIDE_PHOTO_UPLOAD_GRANT";
    static final String TARGET_TYPE = "PHOTO_GRANT_REQUEST";

    private static final List<String> DELIVERY_SLOTS = List.of(
            "BEFORE_INNER",
            "BEFORE_OUTER",
            "AFTER_INNER",
            "AFTER_OUTER");
    private static final List<String> CLEAN_SLOTS = List.of(
            "FIRST_OPEN_INNER",
            "FIRST_OPEN_OUTER",
            "FINAL_CLOSE_INNER",
            "FINAL_CLOSE_OUTER");

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final DeviceConfigurationCanonicalizer canonicalizer;
    private final PhotoGrantWorkVerificationPort workVerification;
    private final DeviceDeploymentTaskRefFactory taskRefFactory;
    private final ReliableDeviceControlTaskRegistrationPort taskRegistration;

    public ReliablePhotoUploadGrantService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            DeviceConfigurationCanonicalizer canonicalizer,
            PhotoGrantWorkVerificationPort workVerification,
            DeviceDeploymentTaskRefFactory taskRefFactory,
            ReliableDeviceControlTaskRegistrationPort taskRegistration) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.canonicalizer = canonicalizer;
        this.workVerification = workVerification;
        this.taskRefFactory = taskRefFactory;
        this.taskRegistration = taskRegistration;
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public PhotoGrantRequestApplyResult apply(
            long tenantId,
            long organizationId,
            long deploymentId,
            long edgeEventId,
            String deploymentCode,
            String eventUid,
            JsonNode payload,
            LocalDateTime deviceOccurredAt,
            LocalDateTime receivedAt) {
        String workType = requiredText(payload, "workType");
        UUID workUid = UUID.fromString(
                requiredText(payload, "workUid"));
        List<String> requestedSlots =
                requestedSlots(payload, workType);
        String reason = requiredText(payload, "reason");
        if (!List.of(
                "INITIAL_GRANT_MISSING",
                "GRANT_EXPIRED",
                "EDGE_RESTARTED",
                "UPLOAD_RETRY").contains(reason)) {
            throw new IllegalArgumentException(
                    "photo grant reason is unsupported");
        }
        PhotoGrantWorkVerification verification =
                workVerification.verify(new TrustedPhotoGrantWork(
                        tenantId,
                        organizationId,
                        deploymentId,
                        workType,
                        workUid,
                        requestedSlots));
        if (verification
                == PhotoGrantWorkVerification.UNKNOWN_WORK) {
            return PhotoGrantRequestApplyResult.conflict(
                    "PHOTO_GRANT_WORK_UNKNOWN");
        }

        List<PriorRequest> prior = jdbc.query("""
                        SELECT event_uid, grant_generation, request_status
                        FROM dev_photo_upload_grant_request
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND work_type = ?
                          AND work_uid = ?
                        ORDER BY grant_generation DESC
                        FOR UPDATE
                        """,
                (rs, ignored) -> new PriorRequest(
                        rs.getString("event_uid"),
                        rs.getLong("grant_generation"),
                        rs.getString("request_status")),
                tenantId,
                organizationId,
                deploymentId,
                workType,
                workUid.toString());
        long generation = prior.isEmpty()
                ? 1
                : Math.addExact(
                        prior.getFirst().generation(), 1);
        for (PriorRequest request : prior) {
            if ("TASK_CREATED".equals(request.status())) {
                taskRegistration.cancelPending(
                        taskRefFactory.issue(
                                tenantId,
                                organizationId,
                                deploymentId),
                        TASK_TYPE,
                        TARGET_TYPE,
                        request.eventUid());
            }
        }
        if (!prior.isEmpty()) {
            jdbc.update("""
                            UPDATE dev_photo_upload_grant_request
                            SET request_status = 'SUPERSEDED',
                                updated_at = ?
                            WHERE tenant_id = ?
                              AND organization_id = ?
                              AND deployment_id = ?
                              AND work_type = ?
                              AND work_uid = ?
                              AND request_status IN (
                                  'TASK_CREATED',
                                  'NO_ACTION_REQUIRED'
                              )
                            """,
                    receivedAt,
                    tenantId,
                    organizationId,
                    deploymentId,
                    workType,
                    workUid.toString());
        }

        UUID commandUid = null;
        UUID taskUid = null;
        String requestStatus;
        if (verification
                == PhotoGrantWorkVerification
                .ALL_REQUESTED_SLOTS_TERMINAL) {
            requestStatus = "NO_ACTION_REQUIRED";
        } else {
            commandUid = UUID.randomUUID();
            Map<String, Object> commandPayload =
                    commandPayload(
                            eventUid,
                            workType,
                            workUid.toString());
            Map<String, Object> envelope = commandEnvelope(
                    commandUid,
                    deploymentCode,
                    eventUid,
                    commandPayload,
                    receivedAt);
            byte[] envelopeSha256 =
                    canonicalizer.payloadSha256(envelope);
            taskUid = taskRegistration.register(
                    new ReliableDeviceControlTaskRegistration(
                            TASK_TYPE,
                            TASK_TYPE + ":"
                                    + eventUid.toUpperCase(),
                            TARGET_TYPE,
                            eventUid,
                            taskRefFactory.issue(
                                    tenantId,
                                    organizationId,
                                    deploymentId),
                            1,
                            objectMapper.writeValueAsString(envelope),
                            envelopeSha256,
                            UUID.fromString(eventUid),
                            UUID.fromString(eventUid),
                            1000));
            requestStatus = "TASK_CREATED";
        }

        requireSingle(jdbc.update("""
                        INSERT INTO dev_photo_upload_grant_request (
                            event_uid,
                            tenant_id, organization_id, deployment_id,
                            edge_event_id, edge_event_type,
                            work_type, work_uid, grant_generation,
                            requested_slots, request_reason,
                            command_uid, reliable_task_uid,
                            request_status,
                            device_occurred_at, backend_received_at,
                            created_at, updated_at
                        ) VALUES (
                            ?,
                            ?, ?, ?,
                            ?, 'PHOTO_UPLOAD_GRANT_REQUESTED',
                            ?, ?, ?,
                            CAST(? AS JSON), ?,
                            ?, ?,
                            ?,
                            ?, ?,
                            ?, ?
                        )
                        """,
                eventUid,
                tenantId,
                organizationId,
                deploymentId,
                edgeEventId,
                workType,
                workUid.toString(),
                generation,
                objectMapper.writeValueAsString(requestedSlots),
                reason,
                nullableUuid(commandUid),
                nullableUuid(taskUid),
                requestStatus,
                deviceOccurredAt,
                receivedAt,
                receivedAt,
                receivedAt),
                "insert photo upload grant request");
        return PhotoGrantRequestApplyResult.applied(
                "NO_ACTION_REQUIRED".equals(requestStatus)
                        ? "NO_ACTION_REQUIRED"
                        : "CREATED");
    }

    private Map<String, Object> commandEnvelope(
            UUID commandUid,
            String deploymentCode,
            String eventUid,
            Map<String, Object> payload,
            LocalDateTime receivedAt) {
        Map<String, Object> target = new LinkedHashMap<>();
        target.put("type", TARGET_TYPE);
        target.put("uid", eventUid);
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("schemaVersion", 1);
        envelope.put("commandUid", commandUid.toString());
        envelope.put("commandType", TASK_TYPE);
        envelope.put("deploymentCode", deploymentCode);
        envelope.put("target", target);
        envelope.put("issuedAt", instant(receivedAt));
        envelope.put(
                "expiresAt",
                instant(receivedAt.plusYears(10)));
        envelope.put("payloadSchemaVersion", 1);
        envelope.put(
                "payloadSha256",
                canonicalizer.hex(
                        canonicalizer.payloadSha256(payload)));
        envelope.put("payload", payload);
        envelope.put("cosGrant", null);
        return envelope;
    }

    private static Map<String, Object> commandPayload(
            String eventUid,
            String workType,
            String workUid) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("grantRequestEventUid", eventUid);
        payload.put("workType", workType);
        payload.put("workUid", workUid);
        payload.put(
                "authorizedSlots",
                "DELIVERY_SESSION".equals(workType)
                        ? DELIVERY_SLOTS
                        : CLEAN_SLOTS);
        return payload;
    }

    private static List<String> requestedSlots(
            JsonNode payload,
            String workType) {
        JsonNode value = payload.get("requestedSlots");
        if (value == null
                || !value.isArray()
                || value.isEmpty()
                || value.size() > 4) {
            throw new IllegalArgumentException(
                    "requestedSlots must contain one to four slots");
        }
        List<String> allowed =
                "DELIVERY_SESSION".equals(workType)
                        ? DELIVERY_SLOTS
                        : "CLEAN_OPERATION".equals(workType)
                        ? CLEAN_SLOTS
                        : List.of();
        List<String> slots = new ArrayList<>();
        int previous = -1;
        for (JsonNode slot : value) {
            if (!slot.isTextual()
                    || !allowed.contains(slot.asText())
                    || allowed.indexOf(slot.asText()) <= previous) {
                throw new IllegalArgumentException(
                        "requestedSlots differ from the work slot order");
            }
            previous = allowed.indexOf(slot.asText());
            slots.add(slot.asText());
        }
        return List.copyOf(slots);
    }

    private static String requiredText(
            JsonNode node,
            String field) {
        JsonNode value = node == null ? null : node.get(field);
        if (value == null
                || !value.isTextual()
                || value.asText().isBlank()) {
            throw new IllegalArgumentException(
                    field + " must be text");
        }
        return value.asText();
    }

    private static String nullableUuid(UUID value) {
        return value == null ? null : value.toString();
    }

    private static String instant(LocalDateTime value) {
        return DateTimeFormatter.ISO_INSTANT.format(
                value.toInstant(ZoneOffset.UTC));
    }

    private static void requireSingle(
            int updated,
            String operation) {
        if (updated != 1) {
            throw new IllegalStateException(
                    operation + " updated " + updated + " rows");
        }
    }

    private record PriorRequest(
            String eventUid,
            long generation,
            String status) {
    }

    public record PhotoGrantRequestApplyResult(
            String effectKind,
            String conflictCode) {

        private static PhotoGrantRequestApplyResult applied(
                String effectKind) {
            return new PhotoGrantRequestApplyResult(
                    effectKind, null);
        }

        private static PhotoGrantRequestApplyResult conflict(
                String conflictCode) {
            return new PhotoGrantRequestApplyResult(
                    null, conflictCode);
        }

        public boolean conflict() {
            return conflictCode != null;
        }
    }
}
