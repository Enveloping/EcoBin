package org.enveloping.ecobin.funds.application.withdrawal;

import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.funds.api.port.FundsOperationalControlPort;
import org.enveloping.ecobin.funds.api.port.MerchantTransferChannelPort;
import org.enveloping.ecobin.funds.api.port.ReliableFundsAttemptBoundaryPort;
import org.enveloping.ecobin.funds.api.port.ReliableFundsTaskRegistrationPort;
import org.enveloping.ecobin.funds.application.access.FundsAccessService;
import org.enveloping.ecobin.funds.application.access.FundsAccessService.WebScope;
import org.enveloping.ecobin.funds.application.authorization.MerchantTransferAuthorizationApplicationService;
import org.enveloping.ecobin.funds.application.pagination.FundsListCursorCodec;
import org.enveloping.ecobin.funds.web.v1.FundsModels.ReleaseWithdrawalConfigurationRequest;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.support.TransactionTemplate;

import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class WithdrawalApplicationServiceTest {

    private JdbcTemplate jdbc;
    private WithdrawalApplicationService service;

    @BeforeEach
    void setUp() {
        jdbc = mock(JdbcTemplate.class);
        FundsAccessService access = mock(FundsAccessService.class);
        when(access.webScope(
                anyBoolean(), anyString(), anyString(),
                anyString(), anyBoolean()))
                .thenReturn(new WebScope(
                        11L,
                        "tenant-test",
                        22L,
                        "organization-test",
                        null,
                        33L,
                        UUID.fromString(
                                "10000000-0000-4000-8000-000000000001"),
                        UUID.fromString(
                                "20000000-0000-4000-8000-000000000001"),
                        "staff-test"));
        service = new WithdrawalApplicationService(
                jdbc,
                access,
                mock(ReliableFundsTaskRegistrationPort.class),
                mock(ReliableFundsAttemptBoundaryPort.class),
                mock(MerchantTransferChannelPort.class),
                mock(TransactionTemplate.class),
                mock(AuditPort.class),
                mock(FundsOperationalControlPort.class),
                mock(FundsListCursorCodec.class),
                mock(MerchantTransferAuthorizationApplicationService.class),
                "https://fake.invalid");
    }

    @Test
    void rejectsReleaseWithoutManualReviewFreeThreshold() {
        assertMissingRequiredV53Field(new ReleaseWithdrawalConfigurationRequest(
                1L,
                "200.00",
                "0.10",
                "200.00",
                null,
                false,
                null,
                null,
                null));
    }

    @Test
    void rejectsReleaseWithoutAutomaticWithdrawalSwitch() {
        assertMissingRequiredV53Field(new ReleaseWithdrawalConfigurationRequest(
                1L,
                "200.00",
                "0.10",
                "200.00",
                "0.00",
                null,
                null,
                null,
                null));
    }

    private void assertMissingRequiredV53Field(
            ReleaseWithdrawalConfigurationRequest request) {
        TargetApiException failure = assertThrows(
                TargetApiException.class,
                () -> service.releaseConfiguration(
                        false,
                        "tenant-test",
                        "organization-test",
                        UUID.randomUUID(),
                        request));

        assertThat(failure.status()).isEqualTo(400);
        assertThat(failure.code()).isEqualTo("COMMON.VALIDATION_FAILED");
        verifyNoInteractions(jdbc);
    }
}
