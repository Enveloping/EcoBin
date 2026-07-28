package org.enveloping.ecobin.integration.fake;

import org.enveloping.ecobin.device.api.port.CosUploadCredentialPort;
import org.enveloping.ecobin.device.api.port.DeviceCommandGateway;
import org.enveloping.ecobin.integration.config.ExternalAdapterModeProperties;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Fake 环境只装配无网络实现。
 */
@Configuration(proxyBeanMethods = false)
@ConditionalOnProperty(
        prefix = "ecobin.external",
        name = "mode",
        havingValue = "fake",
        matchIfMissing = true)
public class FakeExternalAdapterConfiguration {

    @Bean
    DeviceCommandGateway fakeDeviceCommandGateway() {
        return new FakeDeviceCommandGateway();
    }

    @Bean
    CosUploadCredentialPort fakeCosUploadCredentialPort() {
        return new FakeCosUploadCredentialAdapter();
    }

    @Bean
    FakeWechatSessionAdapter fakeWechatMiniappAdapter() {
        return new FakeWechatSessionAdapter();
    }

    @Bean
    FakeExternalIngressBlockFilter fakeExternalIngressBlockFilter(
            ExternalAdapterModeProperties properties) {
        return new FakeExternalIngressBlockFilter(
                properties.getFake().isBlockInbound());
    }
}
