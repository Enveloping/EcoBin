package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.result.DeliveryCompletionResultReference;
import org.enveloping.ecobin.device.api.port.ReliableEdgeConfirmationPort;
import org.enveloping.ecobin.framework.reliability.DeviceDeploymentTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceControlTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceControlTaskRegistrationPort;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;

/**
 * Creates the durable, idempotent protocol-control task that confirms one
 * reliable edge fact. The task is created in the same transaction as the
 * business projection and does not create a physical device-command row.
 */
@Service
public class ReliableEdgeConfirmationService
        implements ReliableEdgeConfirmationPort {

    static final String TASK_TYPE = "CONFIRM_EDGE_EVENT";
    static final String TARGET_TYPE = "BUSINESS_CONFIRMATION";

    private final ObjectMapper objectMapper;
    private final DeviceConfigurationCanonicalizer canonicalizer;
    private final DeviceDeploymentTaskRefFactory taskRefFactory;
    private final ReliableDeviceControlTaskRegistrationPort
            taskRegistrationPort;

    public ReliableEdgeConfirmationService(
            ObjectMapper objectMapper,
            DeviceConfigurationCanonicalizer canonicalizer,
            DeviceDeploymentTaskRefFactory taskRefFactory,
            ReliableDeviceControlTaskRegistrationPort taskRegistrationPort) {
        this.objectMapper = objectMapper;
        this.canonicalizer = canonicalizer;
        this.taskRefFactory = taskRefFactory;
        this.taskRegistrationPort = taskRegistrationPort;
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public UUID registerApplied(
            long tenantId,
            long organizationId,
            long deploymentId,
            String deploymentCode,
            String originalEventUid,
            String originalPayloadSha256,
            String effectKind,
            LocalDateTime processedAt) {
        return registerApplied(
                tenantId,
                organizationId,
                deploymentId,
                deploymentCode,
                originalEventUid,
                originalPayloadSha256,
                effectKind,
                List.of(),
                processedAt);
    }

    @Transactional(propagation = Propagation.MANDATORY)
    @Override
    public UUID registerApplied(
            long tenantId,
            long organizationId,
            long deploymentId,
            String deploymentCode,
            String originalEventUid,
            String originalPayloadSha256,
            String effectKind,
            List<DeliveryCompletionResultReference> resultReferences,
            LocalDateTime processedAt) {
        return register(
                tenantId,
                organizationId,
                deploymentId,
                deploymentCode,
                originalEventUid,
                originalPayloadSha256,
                TASK_TYPE + ":"
                        + originalEventUid.toUpperCase(Locale.ROOT),
                "BUSINESS_APPLIED",
                effectKind,
                null,
                null,
                resultReferences,
                processedAt);
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public UUID registerQuarantined(
            long tenantId,
            long organizationId,
            long deploymentId,
            String deploymentCode,
            String originalEventUid,
            String originalPayloadSha256,
            String errorCode,
            UUID quarantineUid,
            LocalDateTime processedAt) {
        if (errorCode == null
                || !errorCode.matches("[A-Z][A-Z0-9_]{0,63}")) {
            throw new IllegalArgumentException(
                    "errorCode must be a stable safe code");
        }
        return register(
                tenantId,
                organizationId,
                deploymentId,
                deploymentCode,
                originalEventUid,
                originalPayloadSha256,
                TASK_TYPE + ":"
                        + originalEventUid.toUpperCase(Locale.ROOT)
                        + ":"
                        + originalPayloadSha256.toUpperCase(
                        Locale.ROOT),
                "EVENT_QUARANTINED",
                null,
                errorCode,
                quarantineUid,
                List.of(),
                processedAt);
    }

    private UUID register(
            long tenantId,
            long organizationId,
            long deploymentId,
            String deploymentCode,
            String originalEventUid,
            String originalPayloadSha256,
            String taskKey,
            String outcome,
            String effectKind,
            String errorCode,
            UUID quarantineUid,
            List<DeliveryCompletionResultReference> resultReferences,
            LocalDateTime processedAt) {
        UUID confirmationUid = UUID.randomUUID();
        UUID commandUid = UUID.randomUUID();

        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("confirmationUid", confirmationUid.toString());
        payload.put("effectKind", effectKind);
        payload.put("errorCode", errorCode);
        payload.put("originalEventUid", originalEventUid);
        payload.put(
                "originalPayloadSha256", originalPayloadSha256);
        payload.put("outcome", outcome);
        payload.put("processedAt", instant(processedAt));
        payload.put(
                "quarantineUid",
                quarantineUid == null
                        ? null
                        : quarantineUid.toString());
        payload.put(
                "resultReferences",
                resultReferences.stream()
                        .map(reference -> Map.of(
                                "type", reference.type(),
                                "key", reference.key()))
                        .toList());

        Map<String, Object> target = new LinkedHashMap<>();
        target.put("type", "EDGE_EVENT");
        target.put("uid", originalEventUid);
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("schemaVersion", 1);
        envelope.put("commandUid", commandUid.toString());
        envelope.put("commandType", TASK_TYPE);
        envelope.put("deploymentCode", deploymentCode);
        envelope.put("target", target);
        envelope.put("issuedAt", instant(processedAt));
        envelope.put(
                "expiresAt",
                instant(processedAt.plusYears(10)));
        envelope.put("payloadSchemaVersion", 1);
        envelope.put(
                "payloadSha256",
                canonicalizer.hex(
                        canonicalizer.payloadSha256(payload)));
        envelope.put("payload", payload);
        envelope.put("cosGrant", null);

        byte[] envelopeSha256 =
                canonicalizer.payloadSha256(envelope);
        String envelopeJson =
                objectMapper.writeValueAsString(envelope);
        taskRegistrationPort.register(
                new ReliableDeviceControlTaskRegistration(
                        TASK_TYPE,
                        taskKey,
                        TARGET_TYPE,
                        confirmationUid.toString(),
                        taskRefFactory.issue(
                                tenantId,
                                organizationId,
                                deploymentId),
                        1,
                        envelopeJson,
                        envelopeSha256,
                        UUID.fromString(originalEventUid),
                        UUID.fromString(originalEventUid),
                        1000));
        return confirmationUid;
    }

    private static String instant(LocalDateTime value) {
        return DateTimeFormatter.ISO_INSTANT.format(
                value.toInstant(ZoneOffset.UTC));
    }
}
