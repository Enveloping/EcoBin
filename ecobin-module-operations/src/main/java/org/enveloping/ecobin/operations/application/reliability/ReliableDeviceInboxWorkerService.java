package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.device.api.port.TrustedDeviceInboxEventPort;
import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;
import org.enveloping.ecobin.framework.reliability.TrustedOrganizationInboxRefFactory;
import org.enveloping.ecobin.operations.api.reliability.ReliableDeviceInboxWorkerPort;
import org.enveloping.ecobin.operations.api.reliability.ReliableWorkerBatchResult;
import org.enveloping.ecobin.recycling.api.port.ApplyDeliveryCompleteUseCase;
import org.springframework.stereotype.Service;

@Service
public class ReliableDeviceInboxWorkerService
        implements ReliableDeviceInboxWorkerPort {

    private final ReliableInboxTaskRunner runner;
    private final TrustedOrganizationInboxRefFactory inboxRefFactory;
    private final TrustedDeviceInboxEventPort deviceEventPort;
    private final ApplyDeliveryCompleteUseCase deliveryComplete;

    public ReliableDeviceInboxWorkerService(
            ReliableInboxTaskRunner runner,
            TrustedOrganizationInboxRefFactory inboxRefFactory,
            TrustedDeviceInboxEventPort deviceEventPort,
            ApplyDeliveryCompleteUseCase deliveryComplete) {
        this.runner = runner;
        this.inboxRefFactory = inboxRefFactory;
        this.deviceEventPort = deviceEventPort;
        this.deliveryComplete = deliveryComplete;
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
                    TrustedDeviceInboxEvent event =
                            new TrustedDeviceInboxEvent(
                                    inboxRefFactory.issue(
                                            task.inboxId(),
                                            task.tenantId(),
                                            task.organizationId()),
                                    task.messageKind(),
                                    task.normalizedSchemaVersion(),
                                    task.normalizedPayload());
                    TrustedDeviceEventApplyResult applied =
                            "DELIVERY_COMPLETE".equals(
                                    task.messageKind())
                                    ? deliveryComplete.apply(event)
                                    : deviceEventPort.apply(event);
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
