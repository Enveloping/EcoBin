package org.enveloping.ecobin;

import org.enveloping.ecobin.device.api.port.CosUploadCredentialPort;
import org.enveloping.ecobin.device.api.result.CosUploadCredential;
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
    private CosUploadCredentialPort cosUploadCredentialPort;

    @Test
    void stsPlaceholderReturnsNonEmpty() {
        CosUploadCredential credential = cosUploadCredentialPort.issue("SN-TEST-001", 1);
        assertNotNull(credential.tmpSecretId());
        assertNotNull(credential.tmpSecretKey());
        assertNotNull(credential.sessionToken());
        assertTrue(credential.expiredTime() > credential.startTime());
        // 占位模式也有 bucket / region / baseUrl
        assertNotNull(credential.bucket());
        assertNotNull(credential.region());
        assertNotNull(credential.baseUrl());
    }
}
