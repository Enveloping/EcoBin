package org.enveloping.ecobin.identity.application.miniapp;

import org.enveloping.ecobin.identity.api.error.WechatExchangeException;
import org.enveloping.ecobin.identity.api.port.WechatSessionPort;
import org.enveloping.ecobin.identity.api.result.WechatSession;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.web.v1.miniapp.MiniappModels.MiniappLoginRequest;
import org.enveloping.ecobin.identity.web.v1.miniapp.MiniappModels.MiniappSessionCreated;
import org.springframework.stereotype.Service;

import java.util.Map;

/**
 * 微信外调与本地事务的显式分界。
 */
@Service
public class TargetMiniappLoginService {

    private final TargetMiniappLoginTransactionService transactions;
    private final WechatSessionPort wechatSessionPort;

    public TargetMiniappLoginService(
            TargetMiniappLoginTransactionService transactions,
            WechatSessionPort wechatSessionPort) {
        this.transactions = transactions;
        this.wechatSessionPort = wechatSessionPort;
    }

    public MiniappSessionCreated login(MiniappLoginRequest request) {
        MiniappConfiguration configuration =
                transactions.readEnabledConfiguration(request.appId());
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
        String deviceCode = request.registrationSource() == null
                ? null
                : request.registrationSource().deviceCode();
        return transactions.completeLogin(
                configuration.appId(),
                wechatSession.openid(),
                deviceCode);
    }

    public record MiniappConfiguration(
            String appId,
            String appSecret) {
    }
}
