package org.enveloping.ecobin.integration.wechat;

import org.enveloping.ecobin.identity.api.error.WechatExchangeException;
import org.enveloping.ecobin.identity.api.error.MiniappSecretVaultException;
import org.enveloping.ecobin.identity.api.port.MiniappSecretVaultPort;
import org.junit.jupiter.api.Test;

import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class EnvironmentMiniappSecretResolverTest {

    @Test
    void resolvesCredentialThroughTheSharedSecretVault() {
        EnvironmentMiniappSecretResolver resolver =
                new EnvironmentMiniappSecretResolver(
                        vault("local-development-secret"));

        assertEquals(
                "local-development-secret",
                resolver.resolve(
                        "env:ECOBIN_WECHAT_MINIAPP_SECRET"));
    }

    @Test
    void mapsVaultFailuresToWechatDependencyFailures() {
        EnvironmentMiniappSecretResolver resolver =
                new EnvironmentMiniappSecretResolver(
                        vault(null));

        assertThrows(
                WechatExchangeException.class,
                () -> resolver.resolve("wechatSecret"));
    }

    private static MiniappSecretVaultPort vault(String value) {
        return new MiniappSecretVaultPort() {
            @Override
            public String store(
                    UUID operationUid,
                    String secretSha256,
                    String appSecret) {
                throw new UnsupportedOperationException();
            }

            @Override
            public String read(String secretReference) {
                if (value == null) {
                    throw new MiniappSecretVaultException(
                            MiniappSecretVaultException.Reason.UNAVAILABLE,
                            "unavailable");
                }
                return value;
            }
        };
    }
}
