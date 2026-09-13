package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.device.api.port.TrustedDeviceInboxEventPort;
import org.enveloping.ecobin.device.api.port.TrustedDeviceAcceptanceEvidencePort;
import org.enveloping.ecobin.device.api.port.DeviceAcceptanceChallengeCoordinatorPort;
import org.enveloping.ecobin.device.api.port.TrustedDeviceTransportPresencePort;
import org.enveloping.ecobin.device.api.port.TrustedPlatformDeviceAssetFactPort;
import org.enveloping.ecobin.device.api.port.TrustedPlatformConfirmationReceiptPort;
import org.enveloping.ecobin.device.api.result.DeviceTransportPresenceApplyResult;
import org.enveloping.ecobin.device.api.result.DeviceAcceptanceEvidenceApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;
import org.enveloping.ecobin.device.api.result.TrustedDeviceAcceptanceEvent;
import org.enveloping.ecobin.device.api.result.TrustedDeviceTransportEvent;
import org.enveloping.ecobin.device.api.result.TrustedPlatformConfirmationReceiptEvent;
import org.enveloping.ecobin.device.api.result.TrustedPlatformDeviceAssetFactEvent;
import org.enveloping.ecobin.framework.reliability.TrustedOrganizationInboxRefFactory;
import org.enveloping.ecobin.framework.reliability.TrustedPlatformInboxRefFactory;
import org.enveloping.ecobin.operations.api.reliability.ReliableDeviceInboxWorkerPort;
import org.enveloping.ecobin.operations.api.reliability.ReliableWorkerBatchResult;
import org.enveloping.ecobin.recycling.api.port.ApplyDeliveryCompleteUseCase;
import org.enveloping.ecobin.recycling.api.port.ApplyCleanCompleteUseCase;
import org.enveloping.ecobin.recycling.api.port.ApplyFullnessSampleCompleteUseCase;
import org.enveloping.ecobin.recycling.api.port.ApplyFullnessStateChangedUseCase;
import org.springframework.stereotype.Service;

import java.util.Set;

@Service
public class ReliableDeviceInboxWorkerService
        implements ReliableDeviceInboxWorkerPort {

    private static final Set<String> PLATFORM_DEVICE_ASSET_FACTS = Set.of(
            "DEVICE_FAULT_OBSERVED",
            "DEVICE_FAULT_RECOVERED",
            "SAFETY_SENSOR_STATE_CHANGED",
            "DEVICE_COMMAND_OBSERVED",
            "FACTORY_SEAL_COMPLETED",
            "DELIVERY_RECOVERY_QUARANTINED",
            "DELIVERY_ISSUE_ARCHIVED",
            "DELIVERY_ISSUE_EVIDENCE_APPENDED",
            "REMOTE_SUPPORT_TUNNEL_STATUS",
            "MCU_FIRMWARE_UPDATE_PROGRESS",
            "BUSINESS_RUNTIME_UPDATE_PROGRESS",
            "BUSINESS_RUNTIME_UPDATE_CANCEL_RESULT",
            "DEVICE_SOFTWARE_STATE_REPORTED");

    private final ReliableInboxTaskRunner runner;
    private final TrustedPlatformInboxRefFactory platformInboxRefFactory;
    private final TrustedOrganizationInboxRefFactory inboxRefFactory;
    private final TrustedDeviceInboxEventPort deviceEventPort;
    private final TrustedPlatformDeviceAssetFactPort platformDeviceFacts;
    private final TrustedDeviceAcceptanceEvidencePort acceptanceEvidencePort;
    private final TrustedPlatformConfirmationReceiptPort
            platformConfirmationReceiptPort;
    private final TrustedDeviceTransportPresencePort transportPresencePort;
    private final DeviceAcceptanceChallengeCoordinatorPort
            acceptanceChallengeCoordinator;
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
            TrustedPlatformDeviceAssetFactPort platformDeviceFacts,
            TrustedDeviceAcceptanceEvidencePort acceptanceEvidencePort,
            TrustedPlatformConfirmationReceiptPort
                    platformConfirmationReceiptPort,
            TrustedDeviceTransportPresencePort transportPresencePort,
            DeviceAcceptanceChallengeCoordinatorPort
                    acceptanceChallengeCoordinator,
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
        this.platformDeviceFacts = platformDeviceFacts;
        this.acceptanceEvidencePort = acceptanceEvidencePort;
        this.platformConfirmationReceiptPort =
                platformConfirmationReceiptPort;
        this.transportPresencePort = transportPresencePort;
        this.acceptanceChallengeCoordinator =
                acceptanceChallengeCoordinator;
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
                        if ("ONLINE".equals(applied.status())) {
                            acceptanceChallengeCoordinator.requestIfNeeded(
                                    applied.assetId());
                        }
                        return applied.changed()
                                ? InboxTaskHandlerResult.APPLIED
                                : InboxTaskHandlerResult.NO_ACTION_REQUIRED;
                    }
                    if ("DEVICE_ACCEPTANCE_EVIDENCE".equals(
                            task.messageKind())) {
                        if (!"PLATFORM".equals(task.scopeKind())
                                || task.tenantId() != null
                                || task.organizationId() != null) {
                            throw new ReliableTaskInvariantException(
                                    "acceptance evidence inbox task is not platform scoped");
                        }
                        DeviceAcceptanceEvidenceApplyResult applied =
                                acceptanceEvidencePort.apply(
                                        new TrustedDeviceAcceptanceEvent(
                                                platformInboxRefFactory.issue(
                                                        task.inboxId()),
                                                task.normalizedSchemaVersion(),
                                                task.normalizedPayload()));
                        taskGateService.reconcileAsset(applied.assetId());
                        return applied.changed()
                                ? InboxTaskHandlerResult.APPLIED
                                : InboxTaskHandlerResult.NO_ACTION_REQUIRED;
                    }
                    if ("BUSINESS_CONFIRMATION_RECEIPT".equals(
                            task.messageKind())
                            && "PLATFORM".equals(task.scopeKind())) {
                        if (task.tenantId() != null
                                || task.organizationId() != null) {
                            throw new ReliableTaskInvariantException(
                                    "platform confirmation receipt carries "
                                            + "organization keys");
                        }
                        platformConfirmationReceiptPort.apply(
                                new TrustedPlatformConfirmationReceiptEvent(
                                        platformInboxRefFactory.issue(
                                                task.inboxId()),
                                        task.normalizedSchemaVersion(),
                                        task.normalizedPayload()));
                        return InboxTaskHandlerResult.APPLIED;
                    }
                    if (PLATFORM_DEVICE_ASSET_FACTS.contains(
                            task.messageKind())
                            && "PLATFORM".equals(task.scopeKind())) {
                        if (task.tenantId() != null
                                || task.organizationId() != null) {
                            throw new ReliableTaskInvariantException(
                                    "platform device asset fact carries "
                                            + "organization keys");
                        }
                        TrustedDeviceEventApplyResult applied =
                                platformDeviceFacts.apply(
                                        new TrustedPlatformDeviceAssetFactEvent(
                                                platformInboxRefFactory.issue(
                                                        task.inboxId()),
                                                task.messageKind(),
                                                task.normalizedSchemaVersion(),
                                                task.normalizedPayload()));
                        return applied
                                == TrustedDeviceEventApplyResult.APPLIED
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
