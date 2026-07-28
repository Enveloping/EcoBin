package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.device.api.port.TrustedDeviceInboxEventPort;
import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;
import org.enveloping.ecobin.framework.reliability.TrustedOrganizationInboxRefFactory;
import org.enveloping.ecobin.operations.api.reliability.ReliableDeviceInboxWorkerPort;
import org.enveloping.ecobin.operations.api.reliability.ReliableWorkerBatchResult;
import org.springframework.stereotype.Service;

@Service
public class ReliableDeviceInboxWorkerService
        implements ReliableDeviceInboxWorkerPort {

    private final ReliableInboxTaskRunner runner;
    private final TrustedOrganizationInboxRefFactory inboxRefFactory;
    private final TrustedDeviceInboxEventPort deviceEventPort;

    public ReliableDeviceInboxWorkerService(
            ReliableInboxTaskRunner runner,
            TrustedOrganizationInboxRefFactory inboxRefFactory,
            TrustedDeviceInboxEventPort deviceEventPort) {
        this.runner = runner;
        this.inboxRefFactory = inboxRefFactory;
        this.deviceEventPort = deviceEventPort;
    }

    @Override
    public ReliableWorkerBatchResult runBatch(String workerId) {
        ReliableBatchResult result = runner.runBatch(
                ReliableTaskChannel.IOT_DEVICE,
                workerId,
                task -> {
                    if (!"ORGANIZATION".equals(task.scopeKind())
                            || task.tenantId() == null
                            || task.organizationId() == null) {
                        throw new ReliableTaskInvariantException(
                                "device inbox task is not organization scoped");
                    }
                    TrustedDeviceEventApplyResult applied =
                            deviceEventPort.apply(
                                    new TrustedDeviceInboxEvent(
                                            inboxRefFactory.issue(
                                                    task.inboxId(),
                                                    task.tenantId(),
                                                    task.organizationId()),
                                            task.messageKind(),
                                            task.normalizedSchemaVersion(),
                                            task.normalizedPayload()));
                    return applied
                            == TrustedDeviceEventApplyResult.APPLIED
                            ? InboxTaskHandlerResult.APPLIED
                            : InboxTaskHandlerResult.NO_ACTION_REQUIRED;
                });
        return new ReliableWorkerBatchResult(
                result.claimed(),
                result.completed(),
                result.failed());
    }
}
