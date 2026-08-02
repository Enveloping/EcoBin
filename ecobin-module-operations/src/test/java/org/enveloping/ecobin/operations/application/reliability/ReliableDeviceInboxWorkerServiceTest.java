package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.device.api.port.TrustedDeviceInboxEventPort;
import org.enveloping.ecobin.device.api.port.TrustedDeviceTransportPresencePort;
import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.framework.reliability.TrustedOrganizationInboxRef;
import org.enveloping.ecobin.framework.reliability.TrustedOrganizationInboxRefFactory;
import org.enveloping.ecobin.framework.reliability.TrustedPlatformInboxRefFactory;
import org.enveloping.ecobin.recycling.api.port.ApplyCleanCompleteUseCase;
import org.enveloping.ecobin.recycling.api.port.ApplyDeliveryCompleteUseCase;
import org.enveloping.ecobin.recycling.api.port.ApplyFullnessSampleCompleteUseCase;
import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
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
        TrustedDeviceTransportPresencePort transportPresence =
                mock(TrustedDeviceTransportPresencePort.class);
        ReliableDeviceTaskGateService taskGates =
                mock(ReliableDeviceTaskGateService.class);
        CanonicalJson canonicalJson = mock(CanonicalJson.class);
        ApplyDeliveryCompleteUseCase delivery =
                mock(ApplyDeliveryCompleteUseCase.class);
        ApplyCleanCompleteUseCase clean =
                mock(ApplyCleanCompleteUseCase.class);
        ApplyFullnessSampleCompleteUseCase fullness =
                mock(ApplyFullnessSampleCompleteUseCase.class);

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
                        transportPresence,
                        taskGates,
                        canonicalJson,
                        delivery,
                        clean,
                        fullness);

        service.runBatch("worker-a");

        verify(taskGates, never()).reconcileAll();
        verify(taskGates).reconcileHardwareSn("HW-A");
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
                UUID.randomUUID(),
                0L,
                "ORANGE_PI_RUNTIME_SNAPSHOT",
                1,
                "{}",
                now,
                now.plusMinutes(1));
    }
}
