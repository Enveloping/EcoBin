package org.enveloping.ecobin;

import org.enveloping.ecobin.business.dto.PhotoNotifyRequest;
import org.enveloping.ecobin.business.dto.StsTokenRequest;
import org.enveloping.ecobin.business.dto.StsTokenResponse;
import org.enveloping.ecobin.business.entity.CleanOrder;
import org.enveloping.ecobin.business.entity.DeliveryOrder;
import org.enveloping.ecobin.business.mapper.CleanOrderMapper;
import org.enveloping.ecobin.business.mapper.DeliveryOrderMapper;
import org.enveloping.ecobin.business.service.PhotoNotifyService;
import org.enveloping.ecobin.common.exception.BusinessException;
import org.enveloping.ecobin.common.result.Result;
import org.enveloping.ecobin.device.entity.Device;
import org.enveloping.ecobin.device.service.DeviceService;
import org.enveloping.ecobin.framework.cos.CosStsCredential;
import org.enveloping.ecobin.framework.cos.CosTokenClient;
import org.enveloping.ecobin.framework.tenant.TenantContextHolder;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * COS 图片上传 + Photo Notify 验证：
 * <ul>
 *   <li>STS token 占位模式 → 返回非空占位凭证；</li>
 *   <li>投递订单 photo notify → 4 字段回填、已有值不被 null 覆盖；</li>
 *   <li>清运订单 photo notify → 同理；</li>
 *   <li>逐张异步上传（分批次 notify）→ 已填字段保持不变。</li>
 * </ul>
 */
@SpringBootTest
@ActiveProfiles("test")
@Transactional
class PhotoCosTest {

    @Autowired
    private CosTokenClient cosTokenClient;
    @Autowired
    private PhotoNotifyService photoNotifyService;
    @Autowired
    private DeliveryOrderMapper deliveryOrderMapper;
    @Autowired
    private CleanOrderMapper cleanOrderMapper;
    @Autowired
    private DeviceService deviceService;

    private final Long tenantId = 2L;

    @BeforeEach
    void setUp() {
        TenantContextHolder.setTenantId(tenantId);
        TenantContextHolder.setIgnore(true);
    }

    @AfterEach
    void tearDown() {
        TenantContextHolder.clear();
    }

    // ---------- STS Token ----------

    @Test
    void stsPlaceholderReturnsNonEmpty() {
        CosStsCredential credential = cosTokenClient.getTempCredentials("SN-TEST-001", 1);
        assertNotNull(credential.getTmpSecretId());
        assertNotNull(credential.getTmpSecretKey());
        assertNotNull(credential.getSessionToken());
        assertTrue(credential.getExpiredTime() > credential.getStartTime());
        assertEquals("SN-TEST-001/1/", credential.getUploadPrefix());
        // 占位模式也有 bucket / region / baseUrl
        assertNotNull(credential.getBucket());
        assertNotNull(credential.getRegion());
        assertNotNull(credential.getBaseUrl());
    }

    // ---------- Delivery Photo Notify ----------

    @Test
    void deliveryPhotoNotifyFillsUrls() {
        // 手动建一条投递订单（不走两阶段流程，直接 insert）
        DeliveryOrder order = new DeliveryOrder();
        order.setTenantId(tenantId);
        order.setOrderSn("DEL-PHOTO-" + System.nanoTime());
        order.setDeviceId(1L);
        order.setDoorId(1L);
        order.setUserId(1L);
        order.setWasteType1(1);
        order.setWeight(new BigDecimal("0.5"));
        order.setPrice(new BigDecimal("0.10"));
        order.setStatus(0);
        order.setDeliveryStatus(1);
        deliveryOrderMapper.insert(order);

        // 先传前 2 张
        PhotoNotifyRequest req1 = new PhotoNotifyRequest();
        req1.setOrderSn(order.getOrderSn());
        req1.setOrderType(1);
        req1.setPhotoOpenOutside("https://cos.example.com/SN001/1/open_outside.jpg");
        req1.setPhotoOpenInside("https://cos.example.com/SN001/1/open_inside.jpg");
        photoNotifyService.notifyPhotos(req1);

        DeliveryOrder updated = deliveryOrderMapper.selectById(order.getId());
        assertEquals("https://cos.example.com/SN001/1/open_outside.jpg", updated.getPhotoOpenOutside());
        assertEquals("https://cos.example.com/SN001/1/open_inside.jpg", updated.getPhotoOpenInside());
        assertNull(updated.getPhotoCloseOutside());
        assertNull(updated.getPhotoCloseInside());

        // 再传后 2 张，前 2 张不丢
        PhotoNotifyRequest req2 = new PhotoNotifyRequest();
        req2.setOrderSn(order.getOrderSn());
        req2.setOrderType(1);
        req2.setPhotoCloseOutside("https://cos.example.com/SN001/1/close_outside.jpg");
        req2.setPhotoCloseInside("https://cos.example.com/SN001/1/close_inside.jpg");
        photoNotifyService.notifyPhotos(req2);

        DeliveryOrder updated2 = deliveryOrderMapper.selectById(order.getId());
        assertEquals("https://cos.example.com/SN001/1/open_outside.jpg", updated2.getPhotoOpenOutside());
        assertEquals("https://cos.example.com/SN001/1/open_inside.jpg", updated2.getPhotoOpenInside());
        assertEquals("https://cos.example.com/SN001/1/close_outside.jpg", updated2.getPhotoCloseOutside());
        assertEquals("https://cos.example.com/SN001/1/close_inside.jpg", updated2.getPhotoCloseInside());
    }

    @Test
    void deliveryPhotoNotifyNonexistentOrderThrows() {
        PhotoNotifyRequest req = new PhotoNotifyRequest();
        req.setOrderSn("NONEXISTENT-ORDER-SN");
        req.setOrderType(1);
        req.setPhotoOpenOutside("https://example.com/photo.jpg");
        assertThrows(BusinessException.class, () -> photoNotifyService.notifyPhotos(req));
    }

    // ---------- Clean Photo Notify ----------

    @Test
    void cleanPhotoNotifyFillsUrls() {
        CleanOrder order = new CleanOrder();
        order.setTenantId(tenantId);
        order.setOrderSn("CLEAN-PHOTO-" + System.nanoTime());
        order.setDeviceId(1L);
        order.setDoorId(1L);
        order.setUserId(1L);
        order.setWasteType1(1);
        order.setWeight(BigDecimal.ZERO);
        order.setGrossWeight(BigDecimal.ZERO);
        order.setTareWeight(BigDecimal.ZERO);
        order.setNetWeight(BigDecimal.ZERO);
        order.setAuditStatus(1);
        order.setStatus(1);
        cleanOrderMapper.insert(order);

        PhotoNotifyRequest req = new PhotoNotifyRequest();
        req.setOrderSn(order.getOrderSn());
        req.setOrderType(2);
        req.setPhotoOpenOutside("https://cos.example.com/SN001/1/clean_open_out.jpg");
        req.setPhotoCloseInside("https://cos.example.com/SN001/1/clean_close_in.jpg");
        photoNotifyService.notifyPhotos(req);

        CleanOrder updated = cleanOrderMapper.selectById(order.getId());
        assertEquals("https://cos.example.com/SN001/1/clean_open_out.jpg", updated.getPhotoOpenOutside());
        assertNull(updated.getPhotoOpenInside());
        assertNull(updated.getPhotoCloseOutside());
        assertEquals("https://cos.example.com/SN001/1/clean_close_in.jpg", updated.getPhotoCloseInside());
    }

    // ---------- Controller 层（直接调 Service，验证 DTO 映射） ----------

    @Test
    void stsTokenResponseMapsFromCredential() {
        CosStsCredential credential = cosTokenClient.getTempCredentials("SN-MAP", 3);
        StsTokenResponse response = StsTokenResponse.builder()
                .tmpSecretId(credential.getTmpSecretId())
                .tmpSecretKey(credential.getTmpSecretKey())
                .sessionToken(credential.getSessionToken())
                .startTime(credential.getStartTime())
                .expiredTime(credential.getExpiredTime())
                .bucket(credential.getBucket())
                .region(credential.getRegion())
                .baseUrl(credential.getBaseUrl())
                .uploadPrefix(credential.getUploadPrefix())
                .build();
        assertNotNull(response.getTmpSecretId());
        assertEquals("SN-MAP/3/", response.getUploadPrefix());
    }
}
