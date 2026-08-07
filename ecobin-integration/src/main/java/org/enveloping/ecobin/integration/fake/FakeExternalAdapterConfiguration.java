package org.enveloping.ecobin.integration.fake;

import org.enveloping.ecobin.device.api.port.CosUploadCredentialPort;
import org.enveloping.ecobin.funds.api.port.MerchantTransferChannelPort;
import org.enveloping.ecobin.funds.api.port.MerchantTransferAuthorizationChannelPort;
import org.enveloping.ecobin.funds.api.port.NativePaymentChannelPort;
import org.enveloping.ecobin.integration.config.ExternalAdapterModeProperties;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.beans.factory.annotation.Value;

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
    FakeDeviceCommandSubmissionAdapter
            fakeDeviceCommandSubmissionAdapter() {
        return new FakeDeviceCommandSubmissionAdapter();
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
    NativePaymentChannelPort fakeNativePaymentChannelPort(
            @Value("${ecobin.external.fake.wechat-pay.auto-succeed:true}")
            boolean autoSucceed) {
        return new FakeNativePaymentAdapter(autoSucceed);
    }

    @Bean
    FakeMerchantTransferAuthorizationStore
            fakeMerchantTransferAuthorizationStore() {
        return new FakeMerchantTransferAuthorizationStore();
    }

    @Bean
    MerchantTransferAuthorizationChannelPort
            fakeMerchantTransferAuthorizationChannelPort(
            FakeMerchantTransferAuthorizationStore store,
            @Value("${ecobin.external.fake.wechat-transfer-authorization.auto-succeed:true}")
            boolean autoSucceed) {
        return new FakeMerchantTransferAuthorizationAdapter(
                store, autoSucceed);
    }

    @Bean
    MerchantTransferChannelPort fakeMerchantTransferChannelPort(
            @Value("${ecobin.external.fake.wechat-transfer.auto-succeed:true}")
            boolean autoSucceed) {
        return new FakeMerchantTransferAdapter(autoSucceed);
    }

    @Bean
    FakeExternalIngressBlockFilter fakeExternalIngressBlockFilter(
            ExternalAdapterModeProperties properties) {
        return new FakeExternalIngressBlockFilter(
                properties.getFake().isBlockInbound());
    }
}
