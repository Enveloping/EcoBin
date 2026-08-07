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
 * COS 直传凭证验证（后端限制作业目录、设备选择槽位对象 key）：
 * <ul>
 *   <li>STS 凭证占位模式 → 返回非空占位三件套 + bucket/region/baseUrl。</li>
 * </ul>
 * 具体照片 key 由设备按槽位契约生成，URL 随上行事件回传；
 * 真实上传链路由目标纪元的设备/回收集成测试覆盖。
 */
@SpringBootTest
@ActiveProfiles("test")
class PhotoCosTest {

    @Autowired
    private CosUploadCredentialPort cosUploadCredentialPort;

    @Test
    void stsPlaceholderReturnsNonEmpty() {
        CosUploadCredential credential = cosUploadCredentialPort.issue(
                "SN-TEST-001",
                1,
                "ecobin/Dv_0123456789abcdefghijklmn/delivery-session/"
                        + "30000000-0000-4000-8000-000000000001/");
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
