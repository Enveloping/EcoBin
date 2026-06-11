package org.enveloping.ecobin.business.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.business.dto.DeliveryReportRequest;
import org.enveloping.ecobin.business.dto.OpenDoorResult;
import org.enveloping.ecobin.business.entity.DeliveryOrder;
import org.enveloping.ecobin.business.mapper.DeliveryOrderMapper;
import org.enveloping.ecobin.business.service.DeliveryOrderService;
import org.enveloping.ecobin.common.constant.Constants;
import org.enveloping.ecobin.common.exception.BusinessException;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.device.entity.Device;
import org.enveloping.ecobin.device.entity.Door;
import org.enveloping.ecobin.device.service.DeviceCommandService;
import org.enveloping.ecobin.device.service.DeviceService;
import org.enveloping.ecobin.device.service.DoorService;
import org.enveloping.ecobin.framework.cos.CosPhotoKeys;
import org.enveloping.ecobin.framework.cos.CosTokenClient;
import org.enveloping.ecobin.framework.security.SecurityUtils;
import org.enveloping.ecobin.business.service.WalletService;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

@Service
@RequiredArgsConstructor
public class DeliveryOrderServiceImpl extends ServiceImpl<DeliveryOrderMapper, DeliveryOrder> implements DeliveryOrderService {

    private final DoorService doorService;
    private final DeviceService deviceService;
    private final DeviceCommandService deviceCommandService;
    private final WalletService walletService;
    private final CosTokenClient cosTokenClient;

    @Override
    public boolean save(DeliveryOrder order) {
        if (order.getTenantId() == null) {
            order.setTenantId(Constants.DEFAULT_TENANT_ID);
        }
        if (order.getOrderSn() == null) {
            order.setOrderSn("D" + System.currentTimeMillis() + UUID.randomUUID().toString().substring(0, 4));
        }
        return super.save(order);
    }

    @Override
    public OpenDoorResult openDoor(Long doorId) {
        Long userId = SecurityUtils.getCurrentUserId();
        if (userId == null) {
            throw new BusinessException(401, "未登录");
        }
        // 租户拦截器按当前登录用户的租户隔离，仅能开本租户投口
        Door door = doorService.getById(doorId);
        if (door == null) {
            throw new BusinessException(404, "投口不存在");
        }
        if (door.getEnabled() != null && door.getEnabled() == 0) {
            throw new BusinessException(400, "投口已禁用");
        }
        Device device = deviceService.getById(door.getDeviceId());
        if (device == null) {
            throw new BusinessException(404, "设备不存在");
        }

        String deliveryToken = UUID.randomUUID().toString().replace("-", "");
        DeliveryOrder order = new DeliveryOrder();
        order.setTenantId(door.getTenantId());
        order.setDeliveryToken(deliveryToken);
        order.setDeviceId(device.getId());
        order.setDoorId(door.getId());
        order.setUserId(userId);
        order.setWasteType1(door.getWasteType1());
        order.setWasteType2(door.getWasteType2() != null ? door.getWasteType2() : 0);
        order.setStatus(0);             // 正常
        order.setDeliveryStatus(0);     // 进行中（待设备上报回填）
        order.setLoginType(4);          // 二维码扫码
        // 照片 key 由后端按 deliveryToken 确定性生成，开门即预存 4 张照片 URL（设备直传到对应 key，无需回传）
        CosPhotoKeys keys = cosTokenClient.buildPhotoKeys(device.getSn(), door.getDoorIndex(), deliveryToken);
        order.setPhotoOpenOutside(cosTokenClient.toUrl(keys.openOutside()));
        order.setPhotoOpenInside(cosTokenClient.toUrl(keys.openInside()));
        order.setPhotoCloseOutside(cosTokenClient.toUrl(keys.closeOutside()));
        order.setPhotoCloseInside(cosTokenClient.toUrl(keys.closeInside()));
        save(order);

        // 下发开投口指令（当前为占位实现，不阻塞主流程）
        deviceCommandService.sendOpenDoor(device.getSn(), door.getDoorIndex(), deliveryToken);
        return new OpenDoorResult(order.getId(), deliveryToken);
    }

    @Override
    @Transactional
    public void completeDelivery(DeliveryReportRequest request) {
        // 明文 SN 信任：按 SN 反查设备（sn 全局唯一；IoT 链路无租户上下文，拦截器放行全局查询）
        Device device = deviceService.lambdaQuery().eq(Device::getSn, request.getSn()).one();
        if (device == null) {
            throw new BusinessException(404, "设备未注册: " + request.getSn());
        }
        DeliveryOrder order = lambdaQuery()
                .eq(DeliveryOrder::getDeliveryToken, request.getDeliveryToken())
                .one();
        if (order == null) {
            throw new BusinessException(404, "投递记录不存在");
        }
        // 校验记录确属该设备/租户，避免越权回填
        if (!device.getId().equals(order.getDeviceId())
                || !device.getTenantId().equals(order.getTenantId())) {
            throw new BusinessException(400, "投递记录与设备不匹配");
        }
        if (order.getDeliveryStatus() != null && order.getDeliveryStatus() == 1) {
            throw new BusinessException(400, "投递已完成，请勿重复上报");
        }

        DeliveryOrder update = new DeliveryOrder();
        update.setId(order.getId());
        update.setWeight(request.getWeight());
        if (request.getWasteType1() != null) {
            update.setWasteType1(request.getWasteType1());
        }
        if (request.getWasteType2() != null) {
            update.setWasteType2(request.getWasteType2());
        }
        update.setDeliveryStatus(1);    // 已完成

        // 计算返现金额 = 投口单价 × 重量；回填单价，并入账到用户余额（同事务）
        BigDecimal amount = null;
        Door door = order.getDoorId() != null ? doorService.getById(order.getDoorId()) : null;
        if (door != null && door.getPrice() != null && request.getWeight() != null) {
            amount = door.getPrice().multiply(request.getWeight()).setScale(2, RoundingMode.HALF_UP);
            update.setPrice(door.getPrice());
        }
        updateById(update);

        if (amount != null) {
            // 重复上报已在上方拒绝，保证每单仅入账一次
            walletService.income(order.getUserId(), order.getTenantId(), amount, order.getId());
        }
    }

    @Override
    public PageResult<DeliveryOrder> pageOrders(int page, int pageSize) {
        Page<DeliveryOrder> p = new Page<>(page, pageSize);
        Page<DeliveryOrder> result = page(p, new LambdaQueryWrapper<DeliveryOrder>().orderByDesc(DeliveryOrder::getCreateTime));
        return PageResult.of(result.getRecords(), result.getTotal(), page, pageSize);
    }

    @Override
    public PageResult<DeliveryOrder> pageMyOrders(Long userId, int page, int pageSize) {
        Page<DeliveryOrder> p = new Page<>(page, pageSize);
        Page<DeliveryOrder> result = page(p, new LambdaQueryWrapper<DeliveryOrder>()
                .eq(DeliveryOrder::getUserId, userId)
                .orderByDesc(DeliveryOrder::getCreateTime));
        return PageResult.of(result.getRecords(), result.getTotal(), page, pageSize);
    }

    @Override
    public DeliveryOrder getMyOrder(Long userId, Long id) {
        DeliveryOrder order = getById(id);
        // 租户拦截器已限定本租户范围；此处再校验 user_id 归属，越权或不存在统一按"不存在"处理避免信息泄露
        if (order == null || !userId.equals(order.getUserId())) {
            throw new BusinessException(404, "订单不存在");
        }
        return order;
    }

    @Override
    public Map<String, Object> todayOverview() {
        DeliveryOrderMapper mapper = (DeliveryOrderMapper) baseMapper;
        long count = mapper.countToday();
        Double totalWeight = mapper.sumTodayWeight();
        Map<String, Object> overview = new HashMap<>();
        overview.put("deliveryCount", count);
        overview.put("totalWeight", totalWeight != null ? totalWeight : 0.0);
        return overview;
    }
}
