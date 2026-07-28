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
import java.util.Objects;
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
        InboxScope scope = resolveScope(message);
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
                            scope.scopeKind(),
                            scope.tenantId(),
                            scope.organizationId(),
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
                    scope,
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
                inserted.scopeKind(),
                inserted.tenantId(),
                inserted.organizationId(),
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
            InboxScope incomingScope,
            InboxAggregate existing,
            byte[] rawDigest,
            CanonicalJson.CanonicalPayload incomingPayload,
            LocalDateTime now) {
        if (!existing.scopeKind().equals(incomingScope.scopeKind())
                || !Objects.equals(
                        existing.tenantId(), incomingScope.tenantId())
                || !Objects.equals(
                        existing.organizationId(),
                        incomingScope.organizationId())) {
            throw new ReliableTaskInvariantException(
                    "stable inbox identity resolved to a different scope");
        }
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
                    existing.scopeKind(),
                    existing.tenantId(),
                    existing.organizationId(),
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
                    existing.scopeKind(),
                    existing.tenantId(),
                    existing.organizationId(),
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

    private static InboxScope resolveScope(TrustedInboxMessage message) {
        ScopeAccumulator accumulator = new ScopeAccumulator();
        message.scopeResolver().resolve(accumulator::accept);
        return accumulator.result();
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

    private record InboxScope(
            String scopeKind,
            Long tenantId,
            Long organizationId) {
    }

    private static final class ScopeAccumulator {

        private InboxScope scope;

        private void accept(
                String scopeKind,
                Long tenantId,
                Long organizationId) {
            if (scope != null) {
                throw new IllegalStateException(
                        "trusted inbox scope resolver wrote more than once");
            }
            if ("PLATFORM".equals(scopeKind)
                    && tenantId == null
                    && organizationId == null) {
                scope = new InboxScope("PLATFORM", null, null);
                return;
            }
            if ("ORGANIZATION".equals(scopeKind)
                    && tenantId != null && tenantId > 0
                    && organizationId != null && organizationId > 0) {
                scope = new InboxScope(
                        "ORGANIZATION", tenantId, organizationId);
                return;
            }
            throw new IllegalArgumentException(
                    "trusted inbox resolver returned an invalid scope");
        }

        private InboxScope result() {
            if (scope == null) {
                throw new IllegalStateException(
                        "trusted inbox scope resolver did not write a scope");
            }
            return scope;
        }
    }
}
