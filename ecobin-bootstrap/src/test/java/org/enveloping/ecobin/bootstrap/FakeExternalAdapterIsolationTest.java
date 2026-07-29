package org.enveloping.ecobin.bootstrap;

import org.enveloping.ecobin.device.api.port.CosUploadCredentialPort;
import org.enveloping.ecobin.device.api.port.ReliableDeviceCommandSubmissionPort;
import org.enveloping.ecobin.identity.api.port.WechatSessionPort;
import org.enveloping.ecobin.integration.cos.CosTokenClient;
import org.enveloping.ecobin.integration.fake.FakeCosUploadCredentialAdapter;
import org.enveloping.ecobin.integration.fake.FakeDeviceCommandSubmissionAdapter;
import org.enveloping.ecobin.integration.fake.FakeWechatSessionAdapter;
import org.enveloping.ecobin.integration.onenet.inbound.OneNetEventDispatcher;
import org.enveloping.ecobin.integration.onenet.inbound.OneNetMqConsumer;
import org.enveloping.ecobin.integration.onenet.outbound.OneNetClient;
import org.enveloping.ecobin.integration.wechat.WechatMiniappClient;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.ListableBeanFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.web.client.RestTemplate;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertTrue;

@SpringBootTest
@ActiveProfiles("test")
class FakeExternalAdapterIsolationTest {

    @Autowired
    private ListableBeanFactory beanFactory;

    @Autowired
    private ReliableDeviceCommandSubmissionPort
            deviceCommandSubmissionPort;

    @Autowired
    private CosUploadCredentialPort cosUploadCredentialPort;

    @Autowired
    private WechatSessionPort wechatSessionPort;

    @Test
    void fakeModeOnlyInstallsNetworkFreeAdapters() {
        assertInstanceOf(
                FakeDeviceCommandSubmissionAdapter.class,
                deviceCommandSubmissionPort);
        assertInstanceOf(
                FakeCosUploadCredentialAdapter.class,
                cosUploadCredentialPort);
        assertInstanceOf(FakeWechatSessionAdapter.class, wechatSessionPort);

        assertTrue(beanFactory.getBeansOfType(OneNetClient.class).isEmpty());
        assertTrue(beanFactory.getBeansOfType(CosTokenClient.class).isEmpty());
        assertTrue(beanFactory.getBeansOfType(
                WechatMiniappClient.class).isEmpty());
        assertTrue(beanFactory.getBeansOfType(
                OneNetMqConsumer.class).isEmpty());
        assertTrue(beanFactory.getBeansOfType(
                OneNetEventDispatcher.class).isEmpty());
        assertTrue(beanFactory.getBeansOfType(RestTemplate.class).isEmpty());
        assertTrue(beanFactory.getBeansOfType(
                UserDetailsService.class).isEmpty());
    }

    @Test
    void fakeCredentialsAndWechatSessionCannotReachRealChannels() {
        var credential = cosUploadCredentialPort.issue("FAKE-SN", 1);
        assertEquals("https://cos.invalid", credential.baseUrl());

        var session = wechatSessionPort.exchange("", "", "fake:test-user");
        assertTrue(session.openid().startsWith("fake_openid_"));
    }
}
