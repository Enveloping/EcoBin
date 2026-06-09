package org.enveloping.ecobin.business.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.enveloping.ecobin.business.dto.PhotoNotifyRequest;
import org.enveloping.ecobin.business.entity.CleanOrder;
import org.enveloping.ecobin.business.entity.DeliveryOrder;
import org.enveloping.ecobin.business.mapper.CleanOrderMapper;
import org.enveloping.ecobin.business.mapper.DeliveryOrderMapper;
import org.enveloping.ecobin.business.service.PhotoNotifyService;
import org.enveloping.ecobin.common.exception.BusinessException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 照片 URL 回填服务实现。
 * <p>
 * 设备直传 COS 后回报 URL，按 {@code orderSn + orderType} 定位订单并逐非空字段覆盖。
 * 支持逐张异步上传：每次调用只覆盖本次非空的字段，已有 URL 不会被覆盖为 null。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class PhotoNotifyServiceImpl implements PhotoNotifyService {

    private final DeliveryOrderMapper deliveryOrderMapper;
    private final CleanOrderMapper cleanOrderMapper;

    @Override
    @Transactional
    public void notifyPhotos(PhotoNotifyRequest request) {
        switch (request.getOrderType()) {
            case 1 -> notifyDelivery(request);
            case 2 -> notifyClean(request);
            default -> throw new BusinessException("无效的订单类型（1=投递 2=清运）: " + request.getOrderType());
        }
    }

    private void notifyDelivery(PhotoNotifyRequest request) {
        DeliveryOrder order = deliveryOrderMapper.selectOne(
                new LambdaQueryWrapper<DeliveryOrder>().eq(DeliveryOrder::getOrderSn, request.getOrderSn()));
        if (order == null) {
            throw new BusinessException("投递订单不存在: " + request.getOrderSn());
        }
        boolean changed = false;
        if (notBlank(request.getPhotoOpenOutside())) { order.setPhotoOpenOutside(request.getPhotoOpenOutside()); changed = true; }
        if (notBlank(request.getPhotoOpenInside()))  { order.setPhotoOpenInside(request.getPhotoOpenInside());   changed = true; }
        if (notBlank(request.getPhotoCloseOutside())){ order.setPhotoCloseOutside(request.getPhotoCloseOutside()); changed = true; }
        if (notBlank(request.getPhotoCloseInside())) { order.setPhotoCloseInside(request.getPhotoCloseInside()); changed = true; }
        if (changed) {
            deliveryOrderMapper.updateById(order);
            log.info("[PhotoNotify] 投递订单 {} 照片已回填", request.getOrderSn());
        }
    }

    private void notifyClean(PhotoNotifyRequest request) {
        CleanOrder order = cleanOrderMapper.selectOne(
                new LambdaQueryWrapper<CleanOrder>().eq(CleanOrder::getOrderSn, request.getOrderSn()));
        if (order == null) {
            throw new BusinessException("清运订单不存在: " + request.getOrderSn());
        }
        boolean changed = false;
        if (notBlank(request.getPhotoOpenOutside())) { order.setPhotoOpenOutside(request.getPhotoOpenOutside()); changed = true; }
        if (notBlank(request.getPhotoOpenInside()))  { order.setPhotoOpenInside(request.getPhotoOpenInside());   changed = true; }
        if (notBlank(request.getPhotoCloseOutside())){ order.setPhotoCloseOutside(request.getPhotoCloseOutside()); changed = true; }
        if (notBlank(request.getPhotoCloseInside())) { order.setPhotoCloseInside(request.getPhotoCloseInside()); changed = true; }
        if (changed) {
            cleanOrderMapper.updateById(order);
            log.info("[PhotoNotify] 清运订单 {} 照片已回填", request.getOrderSn());
        }
    }

    private static boolean notBlank(String s) {
        return s != null && !s.isBlank();
    }
}
