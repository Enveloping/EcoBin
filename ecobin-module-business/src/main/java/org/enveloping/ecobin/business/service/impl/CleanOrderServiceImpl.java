package org.enveloping.ecobin.business.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.business.dto.CleanGrossRequest;
import org.enveloping.ecobin.business.dto.CleanTareRequest;
import org.enveloping.ecobin.business.entity.CleanBag;
import org.enveloping.ecobin.business.entity.CleanOrder;
import org.enveloping.ecobin.business.mapper.CleanOrderMapper;
import org.enveloping.ecobin.business.service.CleanBagService;
import org.enveloping.ecobin.business.service.CleanOrderService;
import org.enveloping.ecobin.common.constant.Constants;
import org.enveloping.ecobin.common.exception.BusinessException;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.device.entity.Device;
import org.enveloping.ecobin.device.entity.Door;
import org.enveloping.ecobin.device.service.DeviceCommandService;
import org.enveloping.ecobin.device.service.DeviceService;
import org.enveloping.ecobin.device.service.DoorService;
import org.enveloping.ecobin.framework.security.SecurityUtils;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.util.UUID;

@Service
@RequiredArgsConstructor
public class CleanOrderServiceImpl extends ServiceImpl<CleanOrderMapper, CleanOrder> implements CleanOrderService {

    private final DeviceService deviceService;
    private final DoorService doorService;
    private final DeviceCommandService deviceCommandService;
    private final CleanBagService cleanBagService;

    @Override
    public boolean save(CleanOrder order) {
        if (order.getTenantId() == null) {
            order.setTenantId(Constants.DEFAULT_TENANT_ID);
        }
        if (order.getOrderSn() == null) {
            order.setOrderSn("C" + System.currentTimeMillis() + UUID.randomUUID().toString().substring(0, 4));
        }
        if (order.getAuditStatus() == null) {
            order.setAuditStatus(0);   // 默认待审核
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
    public void openCleanDoor(Long doorId, String bagNo) {
        Long userId = SecurityUtils.getCurrentUserId();
        if (userId == null) {
            throw new BusinessException(401, "未登录");
        }
        // 租户拦截器按当前登录清运员的租户隔离，仅能开本租户投口
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
        // 下发开清运门指令（携带新垃圾袋编号），经 OneNet；凭证未到位时为占位日志，不阻塞主流程
        deviceCommandService.sendOpenCleanDoor(device.getSn(), door.getDoorIndex(), bagNo);
    }

    @Override
    public CleanOrder reportGross(CleanGrossRequest request) {
        // 明文 SN 信任：按 SN 反查设备（sn 全局唯一；IoT 链路无租户上下文，拦截器放行全局查询）
        Device device = deviceService.lambdaQuery().eq(Device::getSn, request.getSn()).one();
        if (device == null) {
            throw new BusinessException(404, "设备未注册: " + request.getSn());
        }
        Door door = doorService.lambdaQuery()
                .eq(Door::getDeviceId, device.getId())
                .eq(Door::getDoorIndex, request.getDoorIndex())
                .one();
        if (door == null) {
            throw new BusinessException(404, "投口不存在: doorIndex=" + request.getDoorIndex());
        }
        // 幂等：设备会重复上报同一条记录直到成功，相同 reportSn 直接返回已建记录，避免重复建单
        if (request.getReportSn() != null && !request.getReportSn().isBlank()) {
            CleanOrder existing = lambdaQuery().eq(CleanOrder::getOrderSn, request.getReportSn()).one();
            if (existing != null) {
                return existing;
            }
        }

        // 该投口当前垃圾袋的去皮重量（无记录则按 0，对应首次清运）
        CleanBag bag = cleanBagService.getCurrent(device.getId(), request.getDoorIndex());
        BigDecimal tare = (bag != null && bag.getTareWeight() != null) ? bag.getTareWeight() : BigDecimal.ZERO;
        BigDecimal gross = request.getWeight();
        BigDecimal net = gross.subtract(tare).max(BigDecimal.ZERO);   // 实际清运量，避免去皮异常导致负值

        CleanOrder order = new CleanOrder();
        order.setTenantId(device.getTenantId());
        order.setOrderSn(request.getReportSn());     // 为空时由 save() 生成
        order.setDeviceId(device.getId());
        order.setDoorId(door.getId());
        order.setBagQr(bag != null ? bag.getBagQr() : null);   // 本次清走的是旧袋
        order.setUserId(request.getUserId());
        order.setWasteType1(door.getWasteType1());
        order.setWasteType2(door.getWasteType2() != null ? door.getWasteType2() : 0);
        order.setGrossWeight(gross);
        order.setTareWeight(tare);
        order.setNetWeight(net);
        order.setWeight(net);                        // 兼容旧字段
        order.setAuditStatus(0);                     // 待审核
        order.setStatus(0);                          // 创建
        save(order);
        return order;
    }

    @Override
    public void reportTare(CleanTareRequest request) {
        Device device = deviceService.lambdaQuery().eq(Device::getSn, request.getSn()).one();
        if (device == null) {
            throw new BusinessException(404, "设备未注册: " + request.getSn());
        }
        Door door = doorService.lambdaQuery()
                .eq(Door::getDeviceId, device.getId())
                .eq(Door::getDoorIndex, request.getDoorIndex())
                .one();
        if (door == null) {
            throw new BusinessException(404, "投口不存在: doorIndex=" + request.getDoorIndex());
        }
        // upsert 该投口当前垃圾袋编号与去皮重量（换袋天然幂等：重复上报覆盖为同值）
        cleanBagService.replaceBag(device.getTenantId(), device.getId(), request.getDoorIndex(),
                request.getBagNo(), request.getWeight(), request.getUserId());
    }

    @Override
    public void audit(Long id, Integer auditStatus) {
        CleanOrder order = getById(id);
        if (order == null) {
            throw new BusinessException(404, "清运订单不存在");
        }
        if (auditStatus == null || auditStatus < 0 || auditStatus > 2) {
            throw new BusinessException(400, "审核状态无效（0=待审核 1=通过 2=拒绝）");
        }
        order.setAuditStatus(auditStatus);
        updateById(order);
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
