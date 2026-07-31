package org.enveloping.ecobin.integration.wechat;

import org.enveloping.ecobin.identity.api.error.MiniappSecretVaultException;
import org.enveloping.ecobin.identity.api.error.WechatExchangeException;
import org.enveloping.ecobin.identity.api.port.MiniappSecretVaultPort;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

@Component
@ConditionalOnProperty(
        prefix = "ecobin.external",
        name = "mode",
        havingValue = "real")
final class EnvironmentMiniappSecretResolver
        implements MiniappSecretResolver {

    private final MiniappSecretVaultPort secretVault;

    EnvironmentMiniappSecretResolver(MiniappSecretVaultPort secretVault) {
        this.secretVault = secretVault;
    }

    @Override
    public String resolve(String secretReference) {
        try {
            return secretVault.read(secretReference);
        } catch (MiniappSecretVaultException exception) {
            throw unavailable(exception.getMessage());
        }
    }

    private static WechatExchangeException unavailable(String message) {
        return new WechatExchangeException(
                WechatExchangeException.Reason.SERVICE_UNAVAILABLE,
                message);
    }
}
