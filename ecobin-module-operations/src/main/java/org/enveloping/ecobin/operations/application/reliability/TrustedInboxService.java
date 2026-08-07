package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.operations.api.inbox.TrustedInboxExecutionLane;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxMessage;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxPort;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxReceipt;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxReceiptState;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxRejection;
import org.enveloping.ecobin.operations.infrastructure.config.ReliableTaskProperties;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository.InboxAggregate;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository.NewInbox;
import org.enveloping.ecobin.device.api.port.TrustedDeviceInboxEventPort;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;
import org.enveloping.ecobin.framework.reliability.TrustedOrganizationInboxRefFactory;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.beans.factory.annotation.Autowired;

import java.nio.charset.StandardCharsets;
import java.time.LocalDateTime;
import java.util.Objects;
import java.util.UUID;

/**
 * 外部可信消息的可靠落地点。
 *
 * <p>本服务只负责保存经过适配器认证、规范化的消息，并为需要异步处理的消息建立唯一任务；
 * 它不在 Pulsar 消费线程里直接创建订单。稳定外部身份相同且内容相同视为重复传输，内容
 * 不同则隔离为冲突，绝不让后到消息覆盖先到事实。</p>
 */
@Service
public class TrustedInboxService implements TrustedInboxPort {

    private final ReliableOperationsJdbcRepository repository;
    private final CanonicalJson canonicalJson;
    private final ReliableTaskProperties properties;
    private final ReliableWorkSignal workSignal;
    private final TrustedOrganizationInboxRefFactory inboxRefFactory;
    private final TrustedDeviceInboxEventPort deviceEventPort;
    private final ReliableDeviceTaskGateService taskGateService;

    @Autowired
    public TrustedInboxService(
            ReliableOperationsJdbcRepository repository,
            CanonicalJson canonicalJson,
            ReliableTaskProperties properties,
            ReliableWorkSignal workSignal,
            TrustedOrganizationInboxRefFactory inboxRefFactory,
            TrustedDeviceInboxEventPort deviceEventPort,
            ReliableDeviceTaskGateService taskGateService) {
        this.repository = repository;
        this.canonicalJson = canonicalJson;
        this.properties = properties;
        this.workSignal = workSignal;
        this.inboxRefFactory = inboxRefFactory;
        this.deviceEventPort = deviceEventPort;
        this.taskGateService = taskGateService;
    }

    TrustedInboxService(
            ReliableOperationsJdbcRepository repository,
            CanonicalJson canonicalJson,
            ReliableTaskProperties properties) {
        this(
                repository,
                canonicalJson,
                properties,
                new ReliableWorkSignal(ignored -> { }),
                null,
                null,
                null);
    }

    @Override
    @Transactional(
            propagation = Propagation.REQUIRES_NEW,
            isolation = Isolation.READ_COMMITTED)
    public TrustedInboxReceipt receive(TrustedInboxMessage message) {
        // REQUIRES_NEW 把“允许传输 ACK”的事实压缩到一个短事务：收件箱和处理任务必须
        // 一起提交，后续业务 worker 失败不会迫使 OneNet/Pulsar 重发原始传输消息。
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
            // 唯一键竞争也是正常幂等路径：锁住已存在行，比较规范摘要后决定重用或隔离。
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
        if (isRuntimeTelemetry(message)) {
            // 高频运行快照直接更新诊断投影，不为每一份快照制造可靠处理任务。
            applyRuntimeTelemetry(inserted);
            return telemetryReceipt(
                    inserted.inboxUid(),
                    canonicalPayload.sha256Hex());
        }
        TaskSnapshot taskSnapshot = taskSnapshot(
                inserted.inboxUid(),
                inserted.messageKind(),
                inserted.normalizedContentSha256Hex());
        // 业务事件则登记 PROCESS_INBOX。订单创建发生在 worker 的权威业务事务中，
        // 与当前传输落库事务分开重试，但仍由同一 inboxUid 保持幂等身份。
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
        signal(message.executionLane());
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
        if (isRuntimeTelemetry(message)) {
            if (!"PROCESSED".equals(existing.processingState())) {
                applyRuntimeTelemetry(existing);
            }
            return telemetryReceipt(
                    existing.inboxUid(),
                    incomingPayload.sha256Hex());
        }
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
        signal(message.executionLane());
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

    private void signal(TrustedInboxExecutionLane lane) {
        if (lane == TrustedInboxExecutionLane.DEVICE) {
            workSignal.deviceInbox();
        }
    }

    private static boolean isRuntimeTelemetry(
            TrustedInboxMessage message) {
        return message.executionLane() == TrustedInboxExecutionLane.DEVICE
                && "DEVICE_RUNTIME_SNAPSHOT".equals(message.messageKind());
    }

    private void applyRuntimeTelemetry(InboxAggregate inbox) {
        if (!"ORGANIZATION".equals(inbox.scopeKind())
                || inbox.tenantId() == null
                || inbox.organizationId() == null) {
            throw new ReliableTaskInvariantException(
                    "runtime telemetry inbox is not organization scoped");
        }
        deviceEventPort.apply(new TrustedDeviceInboxEvent(
                inboxRefFactory.issue(
                        inbox.inboxId(),
                        inbox.tenantId(),
                        inbox.organizationId()),
                inbox.messageKind(),
                inbox.normalizedSchemaVersion(),
                inbox.normalizedPayload()));
        LocalDateTime now = repository.databaseNow();
        repository.markInboxProcessed(inbox.inboxId(), now);
        taskGateService.reconcileHardwareSn(
                canonicalJson.trustedDeviceName(
                        inbox.normalizedPayload()));
    }

    private static TrustedInboxReceipt telemetryReceipt(
            UUID inboxUid,
            String normalizedContentSha256) {
        return new TrustedInboxReceipt(
                TrustedInboxReceiptState.TELEMETRY_APPLIED,
                inboxUid,
                null,
                null,
                normalizedContentSha256,
                true);
    }

    @Override
    @Transactional(
            propagation = Propagation.REQUIRES_NEW,
            isolation = Isolation.READ_COMMITTED)
    public UUID quarantine(TrustedInboxRejection rejection) {
        byte[] rawDigest = canonicalJson.sha256(
                rejection.rawTransportBody());
        byte[] externalIdentity = rejection.externalMessageId() == null
                ? new byte[0]
                : rejection.externalMessageId().getBytes(
                StandardCharsets.UTF_8);
        byte[] dedupeKey = canonicalJson.sha256LengthPrefixed(
                rejection.sourceNamespace().getBytes(
                        StandardCharsets.US_ASCII),
                rejection.sourcePrincipalKey().getBytes(
                        StandardCharsets.UTF_8),
                externalIdentity,
                rawDigest,
                rejection.reasonCode().getBytes(
                        StandardCharsets.US_ASCII));
        return repository.upsertRejectedMessage(
                dedupeKey,
                rejection.sourceNamespace(),
                rejection.sourcePrincipalKey(),
                rejection.externalMessageId(),
                rejection.reasonCode(),
                rawDigest,
                rejection.redactedDiagnostic(),
                repository.databaseNow());
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
