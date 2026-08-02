package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.device.api.port.TrustedDeviceInboxEventPort;
import org.enveloping.ecobin.device.api.port.TrustedDeviceTransportPresencePort;
import org.enveloping.ecobin.device.api.result.DeviceTransportPresenceApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;
import org.enveloping.ecobin.device.api.result.TrustedDeviceTransportEvent;
import org.enveloping.ecobin.framework.reliability.TrustedOrganizationInboxRefFactory;
import org.enveloping.ecobin.framework.reliability.TrustedPlatformInboxRefFactory;
import org.enveloping.ecobin.operations.api.reliability.ReliableDeviceInboxWorkerPort;
import org.enveloping.ecobin.operations.api.reliability.ReliableWorkerBatchResult;
import org.enveloping.ecobin.recycling.api.port.ApplyDeliveryCompleteUseCase;
import org.enveloping.ecobin.recycling.api.port.ApplyCleanCompleteUseCase;
import org.enveloping.ecobin.recycling.api.port.ApplyFullnessSampleCompleteUseCase;
import org.enveloping.ecobin.recycling.api.port.ApplyFullnessStateChangedUseCase;
import org.springframework.stereotype.Service;

@Service
public class ReliableDeviceInboxWorkerService
        implements ReliableDeviceInboxWorkerPort {

    private final ReliableInboxTaskRunner runner;
    private final TrustedPlatformInboxRefFactory platformInboxRefFactory;
    private final TrustedOrganizationInboxRefFactory inboxRefFactory;
    private final TrustedDeviceInboxEventPort deviceEventPort;
    private final TrustedDeviceTransportPresencePort transportPresencePort;
    private final ReliableDeviceTaskGateService taskGateService;
    private final CanonicalJson canonicalJson;
    private final ApplyDeliveryCompleteUseCase deliveryComplete;
    private final ApplyCleanCompleteUseCase cleanComplete;
    private final ApplyFullnessSampleCompleteUseCase fullnessComplete;
    private final ApplyFullnessStateChangedUseCase fullnessStateChanged;

    public ReliableDeviceInboxWorkerService(
            ReliableInboxTaskRunner runner,
            TrustedPlatformInboxRefFactory platformInboxRefFactory,
            TrustedOrganizationInboxRefFactory inboxRefFactory,
            TrustedDeviceInboxEventPort deviceEventPort,
            TrustedDeviceTransportPresencePort transportPresencePort,
            ReliableDeviceTaskGateService taskGateService,
            CanonicalJson canonicalJson,
            ApplyDeliveryCompleteUseCase deliveryComplete,
            ApplyCleanCompleteUseCase cleanComplete,
            ApplyFullnessSampleCompleteUseCase fullnessComplete,
            ApplyFullnessStateChangedUseCase fullnessStateChanged) {
        this.runner = runner;
        this.platformInboxRefFactory = platformInboxRefFactory;
        this.inboxRefFactory = inboxRefFactory;
        this.deviceEventPort = deviceEventPort;
        this.transportPresencePort = transportPresencePort;
        this.taskGateService = taskGateService;
        this.canonicalJson = canonicalJson;
        this.deliveryComplete = deliveryComplete;
        this.cleanComplete = cleanComplete;
        this.fullnessComplete = fullnessComplete;
        this.fullnessStateChanged = fullnessStateChanged;
    }

    @Override
    public ReliableWorkerBatchResult runBatch(String workerId) {
        ReliableBatchResult result = runner.runBatch(
                ReliableTaskChannel.IOT_DEVICE,
                workerId,
                task -> {
                    if ("DEVICE_TRANSPORT_STATUS_CHANGED".equals(
                            task.messageKind())) {
                        if (!"PLATFORM".equals(task.scopeKind())
                                || task.tenantId() != null
                                || task.organizationId() != null) {
                            throw new ReliableTaskInvariantException(
                                    "transport lifecycle inbox task is not platform scoped");
                        }
                        DeviceTransportPresenceApplyResult applied =
                                transportPresencePort.apply(
                                        new TrustedDeviceTransportEvent(
                                                platformInboxRefFactory.issue(
                                                        task.inboxId()),
                                                task.normalizedSchemaVersion(),
                                                task.normalizedPayload()));
                        taskGateService.reconcileAsset(applied.assetId());
                        return applied.changed()
                                ? InboxTaskHandlerResult.APPLIED
                                : InboxTaskHandlerResult.NO_ACTION_REQUIRED;
                    }
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
                            switch (task.messageKind()) {
                                case "DELIVERY_COMPLETE" ->
                                        deliveryComplete.apply(event);
                                case "CLEAN_COMPLETE" ->
                                        cleanComplete.apply(event);
                                case "FULLNESS_SAMPLE_COMPLETE" ->
                                        fullnessComplete.apply(event);
                                case "FULLNESS_STATE_CHANGED" ->
                                        fullnessStateChanged.apply(event);
                                default ->
                                        deviceEventPort.apply(event);
                            };
                    taskGateService.reconcileHardwareSn(
                            canonicalJson.trustedDeviceName(
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
