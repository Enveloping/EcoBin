package org.enveloping.ecobin;

import org.enveloping.ecobin.framework.cos.CosStsCredential;
import org.enveloping.ecobin.framework.cos.CosTokenClient;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;

import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * COS 直传凭证验证（设备自定 key 模式）：
 * <ul>
 *   <li>STS 凭证占位模式 → 返回非空占位三件套 + bucket/region/baseUrl。</li>
 * </ul>
 * 照片 key 由设备自定、URL 随上行事件回传（投递/清运一致），后端不再算 key；
 * 照片 URL 入账的端到端验证见 {@code DeliveryTwoPhaseTest} / {@code CleanFlowTest}。
 */
@SpringBootTest
@ActiveProfiles("test")
class PhotoCosTest {

    @Autowired
    private CosTokenClient cosTokenClient;

    @Test
    void stsPlaceholderReturnsNonEmpty() {
        CosStsCredential credential = cosTokenClient.getTempCredentials("SN-TEST-001", 1);
        assertNotNull(credential.getTmpSecretId());
        assertNotNull(credential.getTmpSecretKey());
        assertNotNull(credential.getSessionToken());
        assertTrue(credential.getExpiredTime() > credential.getStartTime());
        // 占位模式也有 bucket / region / baseUrl
        assertNotNull(credential.getBucket());
        assertNotNull(credential.getRegion());
        assertNotNull(credential.getBaseUrl());
    }
}
