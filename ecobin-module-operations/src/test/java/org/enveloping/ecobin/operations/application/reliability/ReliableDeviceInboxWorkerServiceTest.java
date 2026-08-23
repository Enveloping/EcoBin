package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.device.api.port.DeviceAcceptanceChallengeCoordinatorPort;
import org.enveloping.ecobin.device.api.port.TrustedDeviceAcceptanceEvidencePort;
import org.enveloping.ecobin.device.api.port.TrustedDeviceInboxEventPort;
import org.enveloping.ecobin.device.api.port.TrustedDeviceTransportPresencePort;
import org.enveloping.ecobin.device.api.port.TrustedPlatformDeviceAssetFactPort;
import org.enveloping.ecobin.device.api.port.TrustedPlatformConfirmationReceiptPort;
import org.enveloping.ecobin.device.api.result.DeviceAcceptanceEvidenceApplyResult;
import org.enveloping.ecobin.device.api.result.DeviceTransportPresenceApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.framework.reliability.TrustedOrganizationInboxRef;
import org.enveloping.ecobin.framework.reliability.TrustedOrganizationInboxRefFactory;
import org.enveloping.ecobin.framework.reliability.TrustedPlatformInboxRef;
import org.enveloping.ecobin.framework.reliability.TrustedPlatformInboxRefFactory;
import org.enveloping.ecobin.recycling.api.port.ApplyCleanCompleteUseCase;
import org.enveloping.ecobin.recycling.api.port.ApplyDeliveryCompleteUseCase;
import org.enveloping.ecobin.recycling.api.port.ApplyFullnessSampleCompleteUseCase;
import org.enveloping.ecobin.recycling.api.port.ApplyFullnessStateChangedUseCase;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

import java.time.LocalDateTime;
import java.util.Set;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ReliableDeviceInboxWorkerServiceTest {

    @Test
    void organizationDeviceMessageDoesNotTriggerGlobalReconciliation() {
        ReliableInboxTaskRunner runner = mock(ReliableInboxTaskRunner.class);
        TrustedPlatformInboxRefFactory platformFactory =
                mock(TrustedPlatformInboxRefFactory.class);
        TrustedOrganizationInboxRefFactory organizationFactory =
                mock(TrustedOrganizationInboxRefFactory.class);
        TrustedDeviceInboxEventPort deviceEvents =
                mock(TrustedDeviceInboxEventPort.class);
        TrustedDeviceAcceptanceEvidencePort acceptanceEvidence =
                mock(TrustedDeviceAcceptanceEvidencePort.class);
        TrustedPlatformConfirmationReceiptPort platformConfirmations =
                mock(TrustedPlatformConfirmationReceiptPort.class);
        TrustedDeviceTransportPresencePort transportPresence =
                mock(TrustedDeviceTransportPresencePort.class);
        DeviceAcceptanceChallengeCoordinatorPort acceptanceChallenges =
                mock(DeviceAcceptanceChallengeCoordinatorPort.class);
        ReliableDeviceTaskGateService taskGates =
                mock(ReliableDeviceTaskGateService.class);
        CanonicalJson canonicalJson = mock(CanonicalJson.class);
        ApplyDeliveryCompleteUseCase delivery =
                mock(ApplyDeliveryCompleteUseCase.class);
        ApplyCleanCompleteUseCase clean =
                mock(ApplyCleanCompleteUseCase.class);
        ApplyFullnessSampleCompleteUseCase fullness =
                mock(ApplyFullnessSampleCompleteUseCase.class);
        ApplyFullnessStateChangedUseCase fullnessStateChanged =
                mock(ApplyFullnessStateChangedUseCase.class);

        TrustedOrganizationInboxRef inboxRef =
                mock(TrustedOrganizationInboxRef.class);
        when(organizationFactory.issue(41L, 7L, 8L))
                .thenReturn(inboxRef);
        when(deviceEvents.apply(any()))
                .thenReturn(TrustedDeviceEventApplyResult.APPLIED);
        when(canonicalJson.trustedDeviceName(anyString()))
                .thenReturn("HW-A");
        when(runner.runBatch(
                eq(ReliableTaskChannel.IOT_DEVICE),
                anyString(),
                any()))
                .thenAnswer(invocation -> {
                    InboxTaskHandler handler = invocation.getArgument(2);
                    InboxTaskHandlerResult handled = handler.handle(
                            organizationTask());
                    assertEquals(InboxTaskHandlerResult.APPLIED, handled);
                    return new ReliableBatchResult(1, 1, 0);
                });

        ReliableDeviceInboxWorkerService service =
                new ReliableDeviceInboxWorkerService(
                        runner,
                        platformFactory,
                        organizationFactory,
                        deviceEvents,
                        mock(TrustedPlatformDeviceAssetFactPort.class),
                        acceptanceEvidence,
                        platformConfirmations,
                        transportPresence,
                        acceptanceChallenges,
                        taskGates,
                        canonicalJson,
                        delivery,
                        clean,
                        fullness,
                        fullnessStateChanged);

        service.runBatch("worker-a");

        verify(taskGates, never()).reconcileAll();
        verify(taskGates).reconcileHardwareSn("HW-A");
        verify(acceptanceChallenges, never()).requestIfNeeded(anyLong());
    }

    @Test
    void onlinePlatformTransportAutomaticallyRequestsAcceptanceChallenge() {
        ReliableInboxTaskRunner runner = mock(ReliableInboxTaskRunner.class);
        TrustedPlatformInboxRefFactory platformFactory =
                mock(TrustedPlatformInboxRefFactory.class);
        TrustedOrganizationInboxRefFactory organizationFactory =
                mock(TrustedOrganizationInboxRefFactory.class);
        TrustedDeviceInboxEventPort deviceEvents =
                mock(TrustedDeviceInboxEventPort.class);
        TrustedDeviceAcceptanceEvidencePort acceptanceEvidence =
                mock(TrustedDeviceAcceptanceEvidencePort.class);
        TrustedPlatformConfirmationReceiptPort platformConfirmations =
                mock(TrustedPlatformConfirmationReceiptPort.class);
        TrustedDeviceTransportPresencePort transportPresence =
                mock(TrustedDeviceTransportPresencePort.class);
        DeviceAcceptanceChallengeCoordinatorPort acceptanceChallenges =
                mock(DeviceAcceptanceChallengeCoordinatorPort.class);
        ReliableDeviceTaskGateService taskGates =
                mock(ReliableDeviceTaskGateService.class);
        TrustedPlatformInboxRef inboxRef = mock(TrustedPlatformInboxRef.class);
        when(platformFactory.issue(41L)).thenReturn(inboxRef);
        when(transportPresence.apply(any())).thenReturn(
                new DeviceTransportPresenceApplyResult(99L, "ONLINE", true));
        when(runner.runBatch(
                eq(ReliableTaskChannel.IOT_DEVICE), anyString(), any()))
                .thenAnswer(invocation -> {
                    InboxTaskHandler handler = invocation.getArgument(2);
                    assertEquals(InboxTaskHandlerResult.APPLIED,
                            handler.handle(platformTask(
                                    "DEVICE_TRANSPORT_STATUS_CHANGED")));
                    return new ReliableBatchResult(1, 1, 0);
                });

        ReliableDeviceInboxWorkerService service =
                new ReliableDeviceInboxWorkerService(
                        runner,
                        platformFactory,
                        organizationFactory,
                        deviceEvents,
                        mock(TrustedPlatformDeviceAssetFactPort.class),
                        acceptanceEvidence,
                        platformConfirmations,
                        transportPresence,
                        acceptanceChallenges,
                        taskGates,
                        mock(CanonicalJson.class),
                        mock(ApplyDeliveryCompleteUseCase.class),
                        mock(ApplyCleanCompleteUseCase.class),
                        mock(ApplyFullnessSampleCompleteUseCase.class),
                        mock(ApplyFullnessStateChangedUseCase.class));

        service.runBatch("worker-a");

        verify(taskGates).reconcileAsset(99L);
        verify(acceptanceChallenges).requestIfNeeded(99L);
        verify(acceptanceEvidence, never()).apply(any());
    }

    @Test
    void platformAcceptanceEvidenceUpdatesAssetAndDoesNotNeedManualAction() {
        ReliableInboxTaskRunner runner = mock(ReliableInboxTaskRunner.class);
        TrustedPlatformInboxRefFactory platformFactory =
                mock(TrustedPlatformInboxRefFactory.class);
        TrustedOrganizationInboxRefFactory organizationFactory =
                mock(TrustedOrganizationInboxRefFactory.class);
        TrustedDeviceInboxEventPort deviceEvents =
                mock(TrustedDeviceInboxEventPort.class);
        TrustedDeviceAcceptanceEvidencePort acceptanceEvidence =
                mock(TrustedDeviceAcceptanceEvidencePort.class);
        TrustedPlatformConfirmationReceiptPort platformConfirmations =
                mock(TrustedPlatformConfirmationReceiptPort.class);
        TrustedDeviceTransportPresencePort transportPresence =
                mock(TrustedDeviceTransportPresencePort.class);
        DeviceAcceptanceChallengeCoordinatorPort acceptanceChallenges =
                mock(DeviceAcceptanceChallengeCoordinatorPort.class);
        ReliableDeviceTaskGateService taskGates =
                mock(ReliableDeviceTaskGateService.class);
        when(platformFactory.issue(41L))
                .thenReturn(mock(TrustedPlatformInboxRef.class));
        when(acceptanceEvidence.apply(any())).thenReturn(
                new DeviceAcceptanceEvidenceApplyResult(99L, "PASSED", true));
        when(runner.runBatch(
                eq(ReliableTaskChannel.IOT_DEVICE), anyString(), any()))
                .thenAnswer(invocation -> {
                    InboxTaskHandler handler = invocation.getArgument(2);
                    assertEquals(InboxTaskHandlerResult.APPLIED,
                            handler.handle(platformTask(
                                    "DEVICE_ACCEPTANCE_EVIDENCE")));
                    return new ReliableBatchResult(1, 1, 0);
                });

        ReliableDeviceInboxWorkerService service =
                new ReliableDeviceInboxWorkerService(
                        runner,
                        platformFactory,
                        organizationFactory,
                        deviceEvents,
                        mock(TrustedPlatformDeviceAssetFactPort.class),
                        acceptanceEvidence,
                        platformConfirmations,
                        transportPresence,
                        acceptanceChallenges,
                        taskGates,
                        mock(CanonicalJson.class),
                        mock(ApplyDeliveryCompleteUseCase.class),
                        mock(ApplyCleanCompleteUseCase.class),
                        mock(ApplyFullnessSampleCompleteUseCase.class),
                        mock(ApplyFullnessStateChangedUseCase.class));

        service.runBatch("worker-a");

        verify(acceptanceEvidence).apply(any());
        verify(taskGates).reconcileAsset(99L);
        verify(acceptanceChallenges, never()).requestIfNeeded(anyLong());
    }

    @Test
    void platformConfirmationReceiptCompletesWithoutOrganizationScope() {
        ReliableInboxTaskRunner runner = mock(ReliableInboxTaskRunner.class);
        TrustedPlatformInboxRefFactory platformFactory =
                mock(TrustedPlatformInboxRefFactory.class);
        TrustedOrganizationInboxRefFactory organizationFactory =
                mock(TrustedOrganizationInboxRefFactory.class);
        TrustedDeviceInboxEventPort deviceEvents =
                mock(TrustedDeviceInboxEventPort.class);
        TrustedDeviceAcceptanceEvidencePort acceptanceEvidence =
                mock(TrustedDeviceAcceptanceEvidencePort.class);
        TrustedPlatformConfirmationReceiptPort platformConfirmations =
                mock(TrustedPlatformConfirmationReceiptPort.class);
        TrustedDeviceTransportPresencePort transportPresence =
                mock(TrustedDeviceTransportPresencePort.class);
        DeviceAcceptanceChallengeCoordinatorPort acceptanceChallenges =
                mock(DeviceAcceptanceChallengeCoordinatorPort.class);
        ReliableDeviceTaskGateService taskGates =
                mock(ReliableDeviceTaskGateService.class);
        when(platformFactory.issue(41L))
                .thenReturn(mock(TrustedPlatformInboxRef.class));
        when(runner.runBatch(
                eq(ReliableTaskChannel.IOT_DEVICE), anyString(), any()))
                .thenAnswer(invocation -> {
                    InboxTaskHandler handler = invocation.getArgument(2);
                    assertEquals(InboxTaskHandlerResult.APPLIED,
                            handler.handle(platformTask(
                                    "BUSINESS_CONFIRMATION_RECEIPT")));
                    return new ReliableBatchResult(1, 1, 0);
                });

        ReliableDeviceInboxWorkerService service =
                new ReliableDeviceInboxWorkerService(
                        runner,
                        platformFactory,
                        organizationFactory,
                        deviceEvents,
                        mock(TrustedPlatformDeviceAssetFactPort.class),
                        acceptanceEvidence,
                        platformConfirmations,
                        transportPresence,
                        acceptanceChallenges,
                        taskGates,
                        mock(CanonicalJson.class),
                        mock(ApplyDeliveryCompleteUseCase.class),
                        mock(ApplyCleanCompleteUseCase.class),
                        mock(ApplyFullnessSampleCompleteUseCase.class),
                        mock(ApplyFullnessStateChangedUseCase.class));

        service.runBatch("worker-a");

        verify(platformConfirmations).apply(any());
        verify(deviceEvents, never()).apply(any());
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "SAFETY_SENSOR_STATE_CHANGED",
            "REMOTE_SUPPORT_TUNNEL_STATUS",
            "FACTORY_SEAL_COMPLETED"
    })
    void platformDeviceAssetFactCompletesWithoutOrganizationScope(
            String messageKind) {
        ReliableInboxTaskRunner runner = mock(ReliableInboxTaskRunner.class);
        TrustedPlatformInboxRefFactory platformFactory =
                mock(TrustedPlatformInboxRefFactory.class);
        TrustedOrganizationInboxRefFactory organizationFactory =
                mock(TrustedOrganizationInboxRefFactory.class);
        TrustedDeviceInboxEventPort deviceEvents =
                mock(TrustedDeviceInboxEventPort.class);
        TrustedPlatformDeviceAssetFactPort platformDeviceFacts =
                mock(TrustedPlatformDeviceAssetFactPort.class);
        when(platformDeviceFacts.apply(any()))
                .thenReturn(TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED);
        when(platformFactory.issue(41L))
                .thenReturn(mock(TrustedPlatformInboxRef.class));
        when(runner.runBatch(
                eq(ReliableTaskChannel.IOT_DEVICE), anyString(), any()))
                .thenAnswer(invocation -> {
                    InboxTaskHandler handler = invocation.getArgument(2);
                    assertEquals(InboxTaskHandlerResult.NO_ACTION_REQUIRED,
                            handler.handle(platformTask(messageKind)));
                    return new ReliableBatchResult(1, 1, 0);
                });

        ReliableDeviceInboxWorkerService service =
                new ReliableDeviceInboxWorkerService(
                        runner,
                        platformFactory,
                        organizationFactory,
                        deviceEvents,
                        platformDeviceFacts,
                        mock(TrustedDeviceAcceptanceEvidencePort.class),
                        mock(TrustedPlatformConfirmationReceiptPort.class),
                        mock(TrustedDeviceTransportPresencePort.class),
                        mock(DeviceAcceptanceChallengeCoordinatorPort.class),
                        mock(ReliableDeviceTaskGateService.class),
                        mock(CanonicalJson.class),
                        mock(ApplyDeliveryCompleteUseCase.class),
                        mock(ApplyCleanCompleteUseCase.class),
                        mock(ApplyFullnessSampleCompleteUseCase.class),
                        mock(ApplyFullnessStateChangedUseCase.class));

        service.runBatch("worker-a");

        verify(deviceEvents, never()).apply(any());
        verify(platformDeviceFacts).apply(any());
    }

    private static ClaimedInboxTask organizationTask() {
        LocalDateTime now = LocalDateTime.of(
                2026, 8, 2, 0, 0);
        return new ClaimedInboxTask(
                UUID.randomUUID(),
                UUID.randomUUID(),
                41L,
                "ORGANIZATION",
                7L,
                8L,
                UUID.randomUUID(),
                42L,
                UUID.randomUUID(),
                0L,
                "ORANGE_PI_RUNTIME_SNAPSHOT",
                1,
                "{}",
                now,
                now.plusMinutes(1));
    }

    private static ClaimedInboxTask platformTask(String messageKind) {
        LocalDateTime now = LocalDateTime.of(2026, 8, 7, 0, 0);
        return new ClaimedInboxTask(
                UUID.randomUUID(),
                UUID.randomUUID(),
                41L,
                "PLATFORM",
                null,
                null,
                UUID.randomUUID(),
                42L,
                UUID.randomUUID(),
                0L,
                messageKind,
                Set.of(
                        "DEVICE_ACCEPTANCE_EVIDENCE",
                        "BUSINESS_CONFIRMATION_RECEIPT",
                        "DEVICE_FAULT_OBSERVED",
                        "DEVICE_FAULT_RECOVERED",
                        "SAFETY_SENSOR_STATE_CHANGED",
                        "REMOTE_SUPPORT_TUNNEL_STATUS",
                        "FACTORY_SEAL_COMPLETED")
                        .contains(messageKind) ? 2 : 1,
                "{}",
                now,
                now.plusMinutes(1));
    }
}
