package org.enveloping.ecobin.business.service;

import org.enveloping.ecobin.business.dto.PhotoNotifyRequest;

/**
 * 照片 URL 回填服务。
 * <p>
 * 设备直传 COS 后回报 URL，按 {@code orderSn + orderType} 定位订单并逐非空字段覆盖。
 */
public interface PhotoNotifyService {

    /**
     * 回填照片 URL 到对应订单。
     *
     * @param request 照片 URL 通知请求（4 个字段均可空，仅非空值会被写入）
     */
    void notifyPhotos(PhotoNotifyRequest request);
}
