package org.enveloping.ecobin.integration.wechat;

import org.enveloping.ecobin.identity.api.error.WechatExchangeException;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;

import java.util.regex.Pattern;

@Component
@ConditionalOnProperty(
        prefix = "ecobin.external",
        name = "mode",
        havingValue = "real")
final class EnvironmentMiniappSecretResolver
        implements MiniappSecretResolver {

    private static final Pattern ENV_NAME =
            Pattern.compile("^ECOBIN_[A-Z0-9_]{1,120}$");

    private final Environment environment;

    EnvironmentMiniappSecretResolver(Environment environment) {
        this.environment = environment;
    }

    @Override
    public String resolve(String secretReference) {
        if (secretReference == null
                || !secretReference.startsWith("env:")) {
            throw unavailable(
                    "小程序密钥引用不是受支持的外部环境引用");
        }
        String variable = secretReference.substring("env:".length());
        if (!ENV_NAME.matcher(variable).matches()) {
            throw unavailable("小程序密钥环境引用格式无效");
        }
        String value = System.getenv(variable);
        if (value == null || value.isBlank()) {
            value = environment.getProperty(variable);
        }
        if (value == null || value.isBlank()) {
            throw unavailable("小程序密钥暂不可用");
        }
        return value;
    }

    private static WechatExchangeException unavailable(String message) {
        return new WechatExchangeException(
                WechatExchangeException.Reason.SERVICE_UNAVAILABLE,
                message);
    }
}
