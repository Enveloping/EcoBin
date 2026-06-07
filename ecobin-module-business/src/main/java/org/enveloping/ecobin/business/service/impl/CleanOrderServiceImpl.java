package org.enveloping.ecobin.business.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.business.dto.CleanSubmitRequest;
import org.enveloping.ecobin.business.entity.CleanOrder;
import org.enveloping.ecobin.business.mapper.CleanOrderMapper;
import org.enveloping.ecobin.business.service.CleanOrderService;
import org.enveloping.ecobin.common.constant.Constants;
import org.enveloping.ecobin.common.exception.BusinessException;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.device.entity.Device;
import org.enveloping.ecobin.device.entity.Door;
import org.enveloping.ecobin.device.service.DeviceService;
import org.enveloping.ecobin.device.service.DoorService;
import org.springframework.stereotype.Service;

import java.util.UUID;

@Service
@RequiredArgsConstructor
public class CleanOrderServiceImpl extends ServiceImpl<CleanOrderMapper, CleanOrder> implements CleanOrderService {

    private final DeviceService deviceService;
    private final DoorService doorService;

    @Override
    public boolean save(CleanOrder order) {
        if (order.getTenantId() == null) {
            order.setTenantId(Constants.DEFAULT_TENANT_ID);
        }
        if (order.getOrderSn() == null) {
            order.setOrderSn("C" + System.currentTimeMillis() + UUID.randomUUID().toString().substring(0, 4));
        }
        if (order.getAuditStatus() == null) {
            order.setAuditStatus(0);
        }
        if (order.getStatus() == null) {
            order.setStatus(0);
        }
        return super.save(order);
    }

    @Override
    public PageResult<CleanOrder> pageOrders(int page, int pageSize) {
        Page<CleanOrder> p = new Page<>(page, pageSize);
        Page<CleanOrder> result = page(p, new LambdaQueryWrapper<CleanOrder>().orderByDesc(CleanOrder::getCreateTime));
        return PageResult.of(result.getRecords(), result.getTotal(), page, pageSize);
    }

    @Override
    public CleanOrder audit(Long id, Integer auditStatus) {
        CleanOrder order = getById(id);
        if (order == null) {
            throw new BusinessException(404, "清运订单不存在: id=" + id);
        }
        if (order.getAuditStatus() != 0) {
            throw new BusinessException(400, "该订单已审核，不可重复审核");
        }
        CleanOrder update = new CleanOrder();
        update.setId(id);
        update.setAuditStatus(auditStatus);
        update.setStatus(auditStatus == 1 ? 1 : 2);
        updateById(update);
        return getById(id);
    }

    @Override
    public CleanOrder submitClean(Long userId, CleanSubmitRequest request) {
        if (userId == null) {
            throw new BusinessException(401, "未登录");
        }
        // 租户拦截器已按当前登录清运员的租户隔离，跨租户设备自然查不到
        Device device = deviceService.getById(request.getDeviceId());
        if (device == null) {
            throw new BusinessException(404, "设备不存在");
        }
        if (request.getDoorId() != null) {
            Door door = doorService.getById(request.getDoorId());
            if (door == null || !device.getId().equals(door.getDeviceId())) {
                throw new BusinessException(400, "投口与设备不匹配");
            }
        }

        CleanOrder order = new CleanOrder();
        order.setTenantId(device.getTenantId());
        order.setUserId(userId);                 // 一律取登录态，不信任 body
        order.setDeviceId(device.getId());
        order.setDoorId(request.getDoorId());
        order.setWasteType1(request.getWasteType1());
        order.setWasteType2(request.getWasteType2() != null ? request.getWasteType2() : 0);
        order.setWeight(request.getWeight());
        order.setAuditStatus(0);                 // 待租户审核
        order.setStatus(0);                      // 创建
        save(order);
        return order;
    }

    @Override
    public PageResult<CleanOrder> pageMyOrders(Long userId, int page, int pageSize) {
        Page<CleanOrder> p = new Page<>(page, pageSize);
        Page<CleanOrder> result = page(p, new LambdaQueryWrapper<CleanOrder>()
                .eq(CleanOrder::getUserId, userId)
                .orderByDesc(CleanOrder::getCreateTime));
        return PageResult.of(result.getRecords(), result.getTotal(), page, pageSize);
    }

    @Override
    public CleanOrder getMyOrder(Long userId, Long id) {
        CleanOrder order = getById(id);
        // 租户拦截器已限定本租户范围；再校验 user_id 归属，越权或不存在统一按"不存在"处理避免信息泄露
        if (order == null || !userId.equals(order.getUserId())) {
            throw new BusinessException(404, "清运订单不存在");
        }
        return order;
    }
}
