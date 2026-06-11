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
import org.enveloping.ecobin.framework.cos.CosPhotoKeys;
import org.enveloping.ecobin.framework.cos.CosTokenClient;
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
    private final CosTokenClient cosTokenClient;

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
        // 开门即建单：此刻已握有登录清运员 userId 与扫到的新空袋编号；毛重/去皮/净重待设备上报补齐
        CleanOrder order = new CleanOrder();
        order.setTenantId(device.getTenantId());
        order.setDeviceId(device.getId());
        order.setDoorId(door.getId());
        order.setUserId(userId);
        order.setWasteType1(door.getWasteType1());
        order.setWasteType2(door.getWasteType2() != null ? door.getWasteType2() : 0);
        order.setNewBagQr(bagNo);     // 新空袋，待 cleanTare 补去皮重
        order.setAuditStatus(0);      // 待审核
        order.setStatus(0);           // 创建
        save(order);                  // 生成 id + orderSn

        // 照片 key 由后端按 cleanOrderId 确定性生成（id 在 save 后才有），开门即预存 4 张照片 URL（设备直传到对应 key，无需回传）
        CosPhotoKeys keys = cosTokenClient.buildPhotoKeys(device.getSn(), door.getDoorIndex(), String.valueOf(order.getId()));
        order.setPhotoOpenOutside(cosTokenClient.toUrl(keys.openOutside()));
        order.setPhotoOpenInside(cosTokenClient.toUrl(keys.openInside()));
        order.setPhotoCloseOutside(cosTokenClient.toUrl(keys.closeOutside()));
        order.setPhotoCloseInside(cosTokenClient.toUrl(keys.closeInside()));
        updateById(order);

        // 下发开清运门指令（携带 doorIndex 物理控制 + cleanOrderId），经 OneNet；凭证未到位时为占位日志，不阻塞主流程
        deviceCommandService.sendOpenCleanDoor(device.getSn(), door.getDoorIndex(), order.getId());
        return order;
    }

    @Override
    public CleanOrder reportGross(CleanGrossRequest request) {
        Device device = findDeviceBySn(request.getSn());
        CleanOrder order = findOrderForDevice(request.getCleanOrderId(), device);

        // 幂等：设备会重复上报同一条记录直到成功，已回填毛重则直接返回（cleanOrderId 即幂等键）
        if (order.getGrossWeight() != null) {
            return order;
        }

        Door door = doorService.getById(order.getDoorId());
        if (door == null) {
            throw new BusinessException(404, "投口不存在");
        }
        // 该投口当前(旧)垃圾袋的去皮重量（无记录则按 0，对应首次清运）
        CleanBag bag = cleanBagService.getCurrent(device.getId(), door.getDoorIndex());
        BigDecimal tare = (bag != null && bag.getTareWeight() != null) ? bag.getTareWeight() : BigDecimal.ZERO;
        BigDecimal gross = request.getWeight();
        BigDecimal net = gross.subtract(tare).max(BigDecimal.ZERO);   // 实际清运量，避免去皮异常导致负值

        order.setBagQr(bag != null ? bag.getBagQr() : null);   // 本次清走的是旧袋
        order.setGrossWeight(gross);
        order.setTareWeight(tare);
        order.setNetWeight(net);
        order.setWeight(net);                        // 兼容旧字段
        updateById(order);
        return order;
    }

    @Override
    public void reportTare(CleanTareRequest request) {
        Device device = findDeviceBySn(request.getSn());
        CleanOrder order = findOrderForDevice(request.getCleanOrderId(), device);
        Door door = doorService.getById(order.getDoorId());
        if (door == null) {
            throw new BusinessException(404, "投口不存在");
        }
        // upsert 该投口当前垃圾袋编号与去皮重量（新袋编号取自订单，设备不传 bagNo；换袋天然幂等）
        cleanBagService.replaceBag(device.getTenantId(), device.getId(), door.getDoorIndex(),
                order.getNewBagQr(), request.getWeight(), order.getUserId());
    }

    /** 明文 SN 信任：按 SN 反查设备（sn 全局唯一；IoT 链路无租户上下文，拦截器放行全局查询） */
    private Device findDeviceBySn(String sn) {
        Device device = deviceService.lambdaQuery().eq(Device::getSn, sn).one();
        if (device == null) {
            throw new BusinessException(404, "设备未注册: " + sn);
        }
        return device;
    }

    /** 按 cleanOrderId 取订单并校验归属该设备（防串单） */
    private CleanOrder findOrderForDevice(Long cleanOrderId, Device device) {
        CleanOrder order = getById(cleanOrderId);
        if (order == null || !device.getId().equals(order.getDeviceId())) {
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
}
