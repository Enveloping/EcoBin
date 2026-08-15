package org.enveloping.ecobin.identity.application.platformminiapp;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.error.WechatExchangeException;
import org.enveloping.ecobin.identity.api.port.WechatSessionPort;
import org.enveloping.ecobin.identity.api.result.WechatSession;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappLoginService.MiniappConfiguration;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappLoginTransactionService;
import org.enveloping.ecobin.identity.web.v1.miniapp.FactoryMiniappModels.FactoryMiniappLoginRequest;
import org.enveloping.ecobin.identity.web.v1.miniapp.FactoryMiniappModels.FactoryMiniappSessionCreated;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class PlatformMiniappLoginServiceTest {

    private static final String APP_ID = "wx1234567890abcdef";

    @Test
    void reusesSharedChannelConfigurationAndWechatCodeExchange() {
        TargetMiniappLoginTransactionService shared =
                mock(TargetMiniappLoginTransactionService.class);
        PlatformMiniappLoginTransactionService transactions =
                mock(PlatformMiniappLoginTransactionService.class);
        WechatSessionPort wechat = mock(WechatSessionPort.class);
        PlatformMiniappLoginService service =
                new PlatformMiniappLoginService(
                        shared, transactions, wechat);
        FactoryMiniappLoginRequest request =
                new FactoryMiniappLoginRequest(
                        APP_ID, "wx-code", "binding-token");
        FactoryMiniappSessionCreated expected =
                new FactoryMiniappSessionCreated(
                        "jwt",
                        "Bearer",
                        "miniapp-factory",
                        "FACTORY_ACCEPTANCE",
                        Instant.parse("2030-01-01T00:00:00Z"),
                        UUID.randomUUID(),
                        "FACTORY-001",
                        "厂家操作员",
                        List.of("factory.acceptance.read"),
                        true);
        when(shared.readEnabledConfiguration(APP_ID))
                .thenReturn(new MiniappConfiguration(
                        APP_ID, "app-secret"));
        when(wechat.exchange(APP_ID, "app-secret", "wx-code"))
                .thenReturn(new WechatSession("openid", null));
        when(transactions.completeLogin(
                APP_ID, "openid", "binding-token"))
                .thenReturn(expected);

        FactoryMiniappSessionCreated actual = service.login(request);

        assertSame(expected, actual);
        verify(wechat).exchange(APP_ID, "app-secret", "wx-code");
    }

    @Test
    void invalidWechatCodeUsesExistingStableErrorMapping() {
        TargetMiniappLoginTransactionService shared =
                mock(TargetMiniappLoginTransactionService.class);
        PlatformMiniappLoginTransactionService transactions =
                mock(PlatformMiniappLoginTransactionService.class);
        WechatSessionPort wechat = mock(WechatSessionPort.class);
        PlatformMiniappLoginService service =
                new PlatformMiniappLoginService(
                        shared, transactions, wechat);
        when(shared.readEnabledConfiguration(APP_ID))
                .thenReturn(new MiniappConfiguration(
                        APP_ID, "app-secret"));
        when(wechat.exchange(APP_ID, "app-secret", "bad-code"))
                .thenThrow(new WechatExchangeException(
                        WechatExchangeException.Reason.INVALID_CODE,
                        "invalid"));

        TargetApiException failure = assertThrows(
                TargetApiException.class,
                () -> service.login(new FactoryMiniappLoginRequest(
                        APP_ID, "bad-code", null)));

        assertEquals(422, failure.status());
        assertEquals("WECHAT.LOGIN_CODE_INVALID", failure.code());
    }
}
