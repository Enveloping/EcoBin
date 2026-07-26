package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.operations.api.inbox.TrustedInboxExecutionLane;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxMessage;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxPort;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxReceipt;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxReceiptState;
import org.enveloping.ecobin.operations.infrastructure.config.ReliableTaskProperties;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository.InboxAggregate;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository.NewInbox;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.time.LocalDateTime;
import java.util.UUID;

@Service
public class TrustedInboxService implements TrustedInboxPort {

    private final ReliableOperationsJdbcRepository repository;
    private final CanonicalJson canonicalJson;
    private final ReliableTaskProperties properties;

    public TrustedInboxService(
            ReliableOperationsJdbcRepository repository,
            CanonicalJson canonicalJson,
            ReliableTaskProperties properties) {
        this.repository = repository;
        this.canonicalJson = canonicalJson;
        this.properties = properties;
    }

    @Override
    @Transactional(
            propagation = Propagation.REQUIRES_NEW,
            isolation = Isolation.READ_COMMITTED)
    public TrustedInboxReceipt receive(TrustedInboxMessage message) {
        properties.validate();
        CanonicalJson.CanonicalPayload canonicalPayload = canonicalJson.canonicalize(
                message.messageKind(),
                message.normalizedSchemaVersion(),
                message.normalizedPayload());
        byte[] rawBody = message.rawTransportBody();
        byte[] rawDigest = canonicalJson.sha256(rawBody);
        UUID proposedInboxUid = UUID.randomUUID();
        LocalDateTime now = repository.databaseNow();
        try {
            repository.insertInbox(
                    new NewInbox(
                            proposedInboxUid,
                            message.sourceNamespace(),
                            message.sourcePrincipalKey(),
                            message.externalMessageId(),
                            message.messageKind(),
                            message.normalizedSchemaVersion(),
                            rawBody,
                            rawDigest,
                            canonicalPayload.json(),
                            canonicalPayload.sha256(),
                            message.authenticationMethod(),
                            message.authenticationPrincipalRef(),
                            message.correlationUid(),
                            message.causationUid()),
                    now);
        } catch (DuplicateKeyException duplicate) {
            InboxAggregate existing = repository.lockInboxByExternalIdentity(
                            message.sourceNamespace(),
                            message.sourcePrincipalKey(),
                            message.externalMessageId())
                    .orElseThrow(() -> duplicate);
            return handleDuplicate(
                    message,
                    existing,
                    rawDigest,
                    canonicalPayload,
                    now);
        }

        InboxAggregate inserted = repository.lockInboxByUid(proposedInboxUid);
        TaskSnapshot taskSnapshot = taskSnapshot(
                inserted.inboxUid(),
                inserted.messageKind(),
                inserted.normalizedContentSha256Hex());
        UUID taskUid = repository.insertProcessInboxTask(
                inserted.inboxId(),
                inserted.inboxUid(),
                message.executionLane().name(),
                taskSnapshot.json(),
                taskSnapshot.sha256(),
                message.correlationUid(),
                message.causationUid(),
                channelProperties(message.executionLane()).getMaxAutoAttempts(),
                now);
        return accepted(
                TrustedInboxReceiptState.ACCEPTED,
                inserted.inboxUid(),
                taskUid,
                canonicalPayload.sha256Hex());
    }

    private TrustedInboxReceipt handleDuplicate(
            TrustedInboxMessage message,
            InboxAggregate existing,
            byte[] rawDigest,
            CanonicalJson.CanonicalPayload incomingPayload,
            LocalDateTime now) {
        if (!existing.normalizedContentSha256Hex()
                .equals(incomingPayload.sha256Hex())) {
            byte[] dedupeKey = canonicalJson.sha256LengthPrefixed(
                    message.sourceNamespace().getBytes(StandardCharsets.US_ASCII),
                    message.sourcePrincipalKey().getBytes(StandardCharsets.UTF_8),
                    message.externalMessageId().getBytes(StandardCharsets.UTF_8),
                    incomingPayload.sha256(),
                    "IDENTITY_CONTENT_CONFLICT".getBytes(StandardCharsets.US_ASCII));
            UUID quarantineUid = repository.upsertIdentityConflict(
                    dedupeKey,
                    message.sourceNamespace(),
                    message.sourcePrincipalKey(),
                    message.externalMessageId(),
                    existing.inboxId(),
                    rawDigest,
                    incomingPayload.sha256(),
                    now);
            return new TrustedInboxReceipt(
                    TrustedInboxReceiptState.QUARANTINED,
                    existing.inboxUid(),
                    existing.taskUid(),
                    quarantineUid,
                    incomingPayload.sha256Hex(),
                    true);
        }

        repository.touchDuplicate(existing.inboxId(), now);
        TaskSnapshot expectedSnapshot = taskSnapshot(
                existing.inboxUid(),
                existing.messageKind(),
                existing.normalizedContentSha256Hex());
        UUID taskUid = existing.taskUid();
        if (taskUid == null) {
            taskUid = repository.insertProcessInboxTask(
                    existing.inboxId(),
                    existing.inboxUid(),
                    message.executionLane().name(),
                    expectedSnapshot.json(),
                    expectedSnapshot.sha256(),
                    message.correlationUid(),
                    message.causationUid(),
                    channelProperties(message.executionLane()).getMaxAutoAttempts(),
                    now);
        } else {
            if (!message.executionLane().name().equals(existing.executionLane())) {
                throw new ReliableTaskInvariantException(
                        "stable inbox identity resolved to a different execution lane");
            }
            if (!expectedSnapshot.sha256Hex()
                    .equals(existing.taskPayloadSha256Hex())) {
                throw new ReliableTaskInvariantException(
                        "PROCESS_INBOX task payload no longer matches its inbox");
            }
            repository.wakeTask(taskUid, now);
        }
        return accepted(
                TrustedInboxReceiptState.DUPLICATE_ACCEPTED,
                existing.inboxUid(),
                taskUid,
                incomingPayload.sha256Hex());
    }

    private TaskSnapshot taskSnapshot(
            UUID inboxUid, String messageKind, String contentSha256) {
        String payload = """
                {
                  "inboxUid": "%s",
                  "messageKind": "%s",
                  "normalizedContentSha256": "%s"
                }
                """.formatted(inboxUid, messageKind, contentSha256);
        CanonicalJson.CanonicalPayload canonical =
                canonicalJson.canonicalize("PROCESS_INBOX", 1, payload);
        return new TaskSnapshot(
                canonical.json(), canonical.sha256(), canonical.sha256Hex());
    }

    private ReliableTaskProperties.Channel channelProperties(
            TrustedInboxExecutionLane lane) {
        return lane == TrustedInboxExecutionLane.DEVICE
                ? properties.getIotDevice()
                : properties.getFundsWechat();
    }

    private static TrustedInboxReceipt accepted(
            TrustedInboxReceiptState state,
            UUID inboxUid,
            UUID taskUid,
            String normalizedContentSha256) {
        return new TrustedInboxReceipt(
                state,
                inboxUid,
                taskUid,
                null,
                normalizedContentSha256,
                true);
    }

    private record TaskSnapshot(
            String json, byte[] sha256, String sha256Hex) {
    }
}
