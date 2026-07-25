package org.enveloping.ecobin.recycling.application.legacy;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.common.constant.Constants;
import org.enveloping.ecobin.common.exception.BusinessException;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.device.api.legacy.LegacyDeviceAccessPort;
import org.enveloping.ecobin.device.api.legacy.LegacyDeviceSnapshot;
import org.enveloping.ecobin.device.api.legacy.LegacyDoorId;
import org.enveloping.ecobin.device.api.legacy.LegacyDoorSnapshot;
import org.enveloping.ecobin.framework.security.SecurityUtils;
import org.enveloping.ecobin.recycling.api.legacy.LegacyCleanGrossCommand;
import org.enveloping.ecobin.recycling.api.legacy.LegacyCleaningEventPort;
import org.enveloping.ecobin.recycling.api.legacy.LegacyCleaningStatistics;
import org.enveloping.ecobin.recycling.api.legacy.LegacyCleaningStatisticsPort;
import org.enveloping.ecobin.recycling.api.legacy.LegacyCleanTareCommand;
import org.enveloping.ecobin.recycling.domain.legacy.CleanBag;
import org.enveloping.ecobin.recycling.domain.legacy.CleanOrder;
import org.enveloping.ecobin.recycling.infrastructure.persistence.mapper.CleanOrderMapper;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.util.UUID;

@Service
@RequiredArgsConstructor
public class CleanOrderServiceImpl extends ServiceImpl<CleanOrderMapper, CleanOrder>
        implements CleanOrderService, LegacyCleaningEventPort, LegacyCleaningStatisticsPort {

    private final LegacyDeviceAccessPort deviceAccessPort;
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
    public CleanOrder openCleanDoor(Long doorId, String bagNo) {
        Long userId = SecurityUtils.getCurrentUserId();
        if (userId == null) {
            throw new BusinessException(401, "未登录");
        }
        // 租户拦截器按当前登录清运员的租户隔离，仅能开本租户投口
        LegacyDoorSnapshot door = deviceAccessPort.findDoor(new LegacyDoorId(doorId));
        if (door == null) {
            throw new BusinessException(404, "投口不存在");
        }
        if (door.enabled() != null && door.enabled() == 0) {
            throw new BusinessException(400, "投口已禁用");
        }
        LegacyDeviceSnapshot device = deviceAccessPort.findDevice(door.deviceId());
        if (device == null) {
            throw new BusinessException(404, "设备不存在");
        }
        // 开门即建单：此刻已握有登录清运员 userId 与扫到的新空袋编号；毛重/去皮/净重待设备上报补齐
        CleanOrder order = new CleanOrder();
        order.setTenantId(device.tenantId());
        order.setDeviceId(device.id().value());
        order.setDoorId(door.id().value());
        order.setUserId(userId);
        order.setWasteType1(door.wasteType1());
        order.setWasteType2(door.wasteType2() != null ? door.wasteType2() : 0);
        order.setNewBagQr(bagNo);     // 新空袋，待 cleanTare 补去皮重
        order.setAuditStatus(0);      // 待审核
        order.setStatus(0);           // 创建
        save(order);                  // 生成 id + orderSn

        // 照片位置由设备自定（与投递一致）：开门只下发凭证、不预存 key/URL，照片 URL 待设备随 cleanGross 回传

        // 下发开清运门指令（携带 doorIndex 物理控制 + cleanOrderId），经 OneNet；凭证未到位时为占位日志，不阻塞主流程
        deviceAccessPort.openCleanDoor(device.sn(), door.doorIndex(), order.getId());
        return order;
    }

    @Override
    public CleanOrder reportGross(LegacyCleanGrossCommand command) {
        LegacyDeviceSnapshot device = findDeviceBySn(command.sn());
        CleanOrder order = findOrderForDevice(command.cleanOrderId(), device);

        // 幂等：设备会重复上报同一条记录直到成功，已回填毛重则直接返回（cleanOrderId 即幂等键）
        if (order.getGrossWeight() != null) {
            return order;
        }

        LegacyDoorSnapshot door = deviceAccessPort.findDoor(new LegacyDoorId(order.getDoorId()));
        if (door == null) {
            throw new BusinessException(404, "投口不存在");
        }
        // 该投口当前(旧)垃圾袋的去皮重量（无记录则按 0，对应首次清运）
        CleanBag bag = cleanBagService.getCurrent(device.id().value(), door.doorIndex());
        BigDecimal tare = (bag != null && bag.getTareWeight() != null) ? bag.getTareWeight() : BigDecimal.ZERO;
        BigDecimal gross = command.weight();
        BigDecimal net = gross.subtract(tare).max(BigDecimal.ZERO);   // 实际清运量，避免去皮异常导致负值

        order.setBagQr(bag != null ? bag.getBagQr() : null);   // 本次清走的是旧袋
        order.setGrossWeight(gross);
        order.setTareWeight(tare);
        order.setNetWeight(net);
        order.setWeight(net);                        // 兼容旧字段
        // 照片 URL：设备自定位置、直传 COS 后随毛重上报回传，原样存（缺失留空，前端占位）
        order.setPhotoOpenOutside(command.photoOpenOutside());
        order.setPhotoOpenInside(command.photoOpenInside());
        order.setPhotoCloseOutside(command.photoCloseOutside());
        order.setPhotoCloseInside(command.photoCloseInside());
        updateById(order);
        return order;
    }

    @Override
    public void acceptGross(LegacyCleanGrossCommand command) {
        reportGross(command);
    }

    @Override
    public void reportTare(LegacyCleanTareCommand command) {
        LegacyDeviceSnapshot device = findDeviceBySn(command.sn());
        CleanOrder order = findOrderForDevice(command.cleanOrderId(), device);
        LegacyDoorSnapshot door = deviceAccessPort.findDoor(new LegacyDoorId(order.getDoorId()));
        if (door == null) {
            throw new BusinessException(404, "投口不存在");
        }
        // upsert 该投口当前垃圾袋编号与去皮重量（新袋编号取自订单，设备不传 bagNo；换袋天然幂等）
        cleanBagService.replaceBag(device.tenantId(), device.id().value(), door.doorIndex(),
                order.getNewBagQr(), command.weight(), order.getUserId());
    }

    @Override
    public void acceptTare(LegacyCleanTareCommand command) {
        reportTare(command);
    }

    /** 明文 SN 信任：按 SN 反查设备（sn 全局唯一；IoT 链路无租户上下文，拦截器放行全局查询） */
    private LegacyDeviceSnapshot findDeviceBySn(String sn) {
        LegacyDeviceSnapshot device = deviceAccessPort.findDeviceBySn(sn);
        if (device == null) {
            throw new BusinessException(404, "设备未注册: " + sn);
        }
        return device;
    }

    /** 按 cleanOrderId 取订单并校验归属该设备（防串单） */
    private CleanOrder findOrderForDevice(Long cleanOrderId, LegacyDeviceSnapshot device) {
        CleanOrder order = getById(cleanOrderId);
        if (order == null || !device.id().value().equals(order.getDeviceId())) {
            throw new BusinessException(404, "清运订单不存在: " + cleanOrderId);
        }
        return order;
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

    @Override
    public LegacyCleaningStatistics statistics() {
        Double monthWeight = baseMapper.sumMonthWeight();
        return new LegacyCleaningStatistics(
                baseMapper.countMonth(),
                monthWeight == null ? 0.0 : monthWeight);
    }
}
