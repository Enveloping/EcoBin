package org.enveloping.ecobin.identity.application.platformminiapp;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.error.WechatExchangeException;
import org.enveloping.ecobin.identity.api.port.WechatSessionPort;
import org.enveloping.ecobin.identity.api.result.WechatSession;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappLoginService.MiniappConfiguration;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappLoginTransactionService;
import org.enveloping.ecobin.identity.web.v1.miniapp.FactoryMiniappModels.FactoryMiniappLoginRequest;
import org.enveloping.ecobin.identity.web.v1.miniapp.FactoryMiniappModels.FactoryMiniappSessionCreated;
import org.springframework.stereotype.Service;

import java.util.Map;

@Service
public class PlatformMiniappLoginService {

    private final TargetMiniappLoginTransactionService sharedTransactions;
    private final PlatformMiniappLoginTransactionService transactions;
    private final WechatSessionPort wechatSessionPort;

    public PlatformMiniappLoginService(
            TargetMiniappLoginTransactionService sharedTransactions,
            PlatformMiniappLoginTransactionService transactions,
            WechatSessionPort wechatSessionPort) {
        this.sharedTransactions = sharedTransactions;
        this.transactions = transactions;
        this.wechatSessionPort = wechatSessionPort;
    }

    public FactoryMiniappSessionCreated login(
            FactoryMiniappLoginRequest request) {
        MiniappConfiguration configuration =
                sharedTransactions.readEnabledConfiguration(
                        request.appId());
        WechatSession wechatSession;
        try {
            wechatSession = wechatSessionPort.exchange(
                    configuration.appId(),
                    configuration.appSecret(),
                    request.wxLoginCode());
        } catch (WechatExchangeException exception) {
            if (exception.reason()
                    == WechatExchangeException.Reason.INVALID_CODE) {
                throw new TargetApiException(
                        422,
                        "WECHAT.LOGIN_CODE_INVALID",
                        "微信登录动态码无效");
            }
            throw new TargetApiException(
                    503,
                    "WECHAT.LOGIN_SERVICE_UNAVAILABLE",
                    "微信登录服务暂不可用",
                    true,
                    Map.of());
        }
        return transactions.completeLogin(
                configuration.appId(),
                wechatSession.openid(),
                request.bindingToken());
    }
}
