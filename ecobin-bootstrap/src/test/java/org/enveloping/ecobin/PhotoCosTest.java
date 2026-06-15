package org.enveloping.ecobin;

import org.enveloping.ecobin.framework.cos.CosPhotoKeys;
import org.enveloping.ecobin.framework.cos.CosStsCredential;
import org.enveloping.ecobin.framework.cos.CosTokenClient;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * COS 直传凭证 + 照片 key 生成验证（后端定 key 模式）：
 * <ul>
 *   <li>STS 凭证占位模式 → 返回非空占位三件套 + bucket/region/baseUrl（不含 uploadPrefix）；</li>
 *   <li>buildPhotoKeys → 按 {@code sn/doorIndex/token/<slot>.jpg} 确定性生成 4 个 key；</li>
 *   <li>toUrl → baseUrl + "/" + key。</li>
 * </ul>
 * 照片 URL 的「开门即存订单」端到端验证见 {@code DeliveryTwoPhaseTest} / {@code CleanFlowTest}。
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

    @Test
    void buildPhotoKeysIsDeterministicAndScopedByToken() {
        CosPhotoKeys keys = cosTokenClient.buildPhotoKeys("SN-MAP", 3, "TOKEN-XYZ");
        assertEquals("SN-MAP/3/TOKEN-XYZ/open_outside.jpg", keys.openOutside());
        assertEquals("SN-MAP/3/TOKEN-XYZ/open_inside.jpg", keys.openInside());
        assertEquals("SN-MAP/3/TOKEN-XYZ/close_outside.jpg", keys.closeOutside());
        assertEquals("SN-MAP/3/TOKEN-XYZ/close_inside.jpg", keys.closeInside());

        // 同投口不同订单 token → key 互不冲突
        CosPhotoKeys other = cosTokenClient.buildPhotoKeys("SN-MAP", 3, "TOKEN-OTHER");
        assertTrue(!other.openOutside().equals(keys.openOutside()));
    }

    @Test
    void toUrlPrependsBaseUrl() {
        CosStsCredential credential = cosTokenClient.getTempCredentials("SN", 1);
        String url = cosTokenClient.toUrl("SN/1/T/open_outside.jpg");
        assertEquals(credential.getBaseUrl() + "/" + "SN/1/T/open_outside.jpg", url);
    }
}
