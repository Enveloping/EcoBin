package org.enveloping.ecobin.recycling.web.v1;

import org.enveloping.ecobin.recycling.application.clean.CleanDeviceQueryService;
import org.enveloping.ecobin.recycling.application.clean.CleanQueryService;
import org.enveloping.ecobin.recycling.application.clean.CleanRecordQueryService;
import org.enveloping.ecobin.recycling.application.clean.InterruptedCleanBagRecoveryService;
import org.enveloping.ecobin.recycling.application.clean.StartCleanOperationService;
import org.enveloping.ecobin.recycling.web.v1.CleanModels.InterruptedCleanBagRecoveryAccepted;
import org.enveloping.ecobin.recycling.web.v1.CleanModels.RecoverInterruptedCleanBagRequest;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.mock.web.MockHttpServletRequest;

import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class MiniappCleanControllerTest {

    @Mock
    private StartCleanOperationService startService;
    @Mock
    private CleanQueryService queryService;
    @Mock
    private CleanDeviceQueryService deviceQueryService;
    @Mock
    private CleanRecordQueryService recordQueryService;
    @Mock
    private InterruptedCleanBagRecoveryService bagRecoveryService;

    private MiniappCleanController controller;

    @BeforeEach
    void setUp() {
        controller = new MiniappCleanController(
                startService,
                queryService,
                deviceQueryService,
                recordQueryService,
                bagRecoveryService);
    }

    @Test
    void interruptedBagRecoveryKeepsTheOriginalIntentAndReturnsAccepted() {
        UUID idempotencyKey = UUID.fromString(
                "11111111-1111-4111-8111-111111111111");
        UUID operationUid = UUID.fromString(
                "22222222-2222-4222-8222-222222222222");
        UUID recoveryUid = UUID.fromString(
                "33333333-3333-4333-8333-333333333333");
        RecoverInterruptedCleanBagRequest body =
                new RecoverInterruptedCleanBagRequest(
                        "EB1-OLD-BAG-0001",
                        true,
                        false,
                        7L,
                        "现场确认仍为原旧袋");
        InterruptedCleanBagRecoveryAccepted accepted =
                new InterruptedCleanBagRecoveryAccepted(
                        recoveryUid,
                        operationUid,
                        "COMPLETED",
                        "RETAIN_OLD_BAG",
                        "EB1-OLD-BAG-0001",
                        null,
                        null,
                        "WAIT_FOR_NEXT_BUSINESS");
        when(bagRecoveryService.recover(
                idempotencyKey,
                operationUid,
                body)).thenReturn(accepted);
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.addHeader("X-Request-ID", "clean-recovery-request");

        var response = controller.recoverBag(
                idempotencyKey,
                operationUid,
                body,
                request);

        assertThat(response.getStatusCode().value()).isEqualTo(202);
        assertThat(response.getBody()).isNotNull();
        assertThat(response.getBody().requestId())
                .isEqualTo("clean-recovery-request");
        assertThat(response.getBody().data()).isEqualTo(accepted);
        verify(bagRecoveryService).recover(
                idempotencyKey,
                operationUid,
                body);
    }
}
