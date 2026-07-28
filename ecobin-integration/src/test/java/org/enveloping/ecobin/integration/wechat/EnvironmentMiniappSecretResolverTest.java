package org.enveloping.ecobin.integration.wechat;

import org.enveloping.ecobin.identity.api.error.WechatExchangeException;
import org.junit.jupiter.api.Test;
import org.springframework.mock.env.MockEnvironment;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class EnvironmentMiniappSecretResolverTest {

    @Test
    void resolvesCredentialFromSpringConfigWhenOsEnvironmentIsAbsent() {
        MockEnvironment environment = new MockEnvironment()
                .withProperty(
                        "ECOBIN_WECHAT_MINIAPP_SECRET",
                        "local-development-secret");
        EnvironmentMiniappSecretResolver resolver =
                new EnvironmentMiniappSecretResolver(environment);

        assertEquals(
                "local-development-secret",
                resolver.resolve(
                        "env:ECOBIN_WECHAT_MINIAPP_SECRET"));
    }

    @Test
    void rejectsReferencesOutsideTheExplicitEnvironmentNamespace() {
        EnvironmentMiniappSecretResolver resolver =
                new EnvironmentMiniappSecretResolver(
                        new MockEnvironment());

        assertThrows(
                WechatExchangeException.class,
                () -> resolver.resolve("wechatSecret"));
    }
}
