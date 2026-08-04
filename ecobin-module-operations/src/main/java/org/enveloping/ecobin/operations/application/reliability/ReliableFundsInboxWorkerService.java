package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.framework.reliability.TrustedOrganizationInboxRefFactory;
import org.enveloping.ecobin.funds.api.port.WechatPayNotificationPort;
import org.enveloping.ecobin.operations.api.reliability.ReliableFundsInboxWorkerPort;
import org.enveloping.ecobin.operations.api.reliability.ReliableWorkerBatchResult;
import org.springframework.stereotype.Service;

@Service
public class ReliableFundsInboxWorkerService
        implements ReliableFundsInboxWorkerPort {

    private final ReliableInboxTaskRunner runner;
    private final TrustedOrganizationInboxRefFactory inboxRefFactory;
    private final WechatPayNotificationPort notificationPort;

    public ReliableFundsInboxWorkerService(
            ReliableInboxTaskRunner runner,
            TrustedOrganizationInboxRefFactory inboxRefFactory,
            WechatPayNotificationPort notificationPort) {
        this.runner = runner;
        this.inboxRefFactory = inboxRefFactory;
        this.notificationPort = notificationPort;
    }

    @Override
    public ReliableWorkerBatchResult runBatch(String workerId) {
        ReliableBatchResult result = runner.runBatch(
                ReliableTaskChannel.FUNDS_WECHAT,
                workerId,
                task -> {
                    if (!"ORGANIZATION".equals(task.scopeKind())
                            || task.tenantId() == null
                            || task.organizationId() == null) {
                        throw new ReliableTaskInvariantException(
                                "funds inbox task is not organization scoped");
                    }
                    boolean changed = notificationPort.apply(
                            inboxRefFactory.issue(
                                    task.inboxId(), task.tenantId(),
                                    task.organizationId()),
                            task.sourceTaskAttemptId(),
                            task.messageKind(),
                            task.normalizedSchemaVersion(),
                            task.normalizedPayload());
                    return changed
                            ? InboxTaskHandlerResult.APPLIED
                            : InboxTaskHandlerResult.NO_ACTION_REQUIRED;
                });
        return new ReliableWorkerBatchResult(
                result.claimed(), result.completed(), result.failed());
    }
}
