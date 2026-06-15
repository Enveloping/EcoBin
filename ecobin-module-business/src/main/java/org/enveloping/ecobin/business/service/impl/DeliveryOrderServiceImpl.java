package org.enveloping.ecobin.business.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.enveloping.ecobin.business.dto.DeliveryReportRequest;
import org.enveloping.ecobin.business.entity.DeliveryOrder;
import org.enveloping.ecobin.business.mapper.DeliveryOrderMapper;
import org.enveloping.ecobin.business.service.DeliveryOrderService;
import org.enveloping.ecobin.common.constant.Constants;
import org.enveloping.ecobin.common.exception.BusinessException;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.device.entity.Device;
import org.enveloping.ecobin.device.entity.DeviceSession;
import org.enveloping.ecobin.device.entity.Door;
import org.enveloping.ecobin.device.service.DeviceCommandService;
import org.enveloping.ecobin.device.service.DeviceService;
import org.enveloping.ecobin.device.service.DeviceSessionService;
import org.enveloping.ecobin.device.service.DoorService;
import org.enveloping.ecobin.framework.security.SecurityUtils;
import org.enveloping.ecobin.business.service.WalletService;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

@Slf4j
@Service
@RequiredArgsConstructor
public class DeliveryOrderServiceImpl extends ServiceImpl<DeliveryOrderMapper, DeliveryOrder> implements DeliveryOrderService {

    private final DoorService doorService;
    private final DeviceService deviceService;
    private final DeviceCommandService deviceCommandService;
    private final DeviceSessionService deviceSessionService;
    private final WalletService walletService;

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
    public void openDoor(Long doorId) {
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

        // 不建单：仅记「设备当前活跃用户」会话（loginType=4 二维码扫码），建单移到设备上传之后。
        deviceSessionService.activate(device.getId(), door.getTenantId(), userId, 4);

        // 下发开投口指令（仅含 COS 凭证；token/照片 key 由设备每次开门自生成）
        deviceCommandService.sendOpenDoor(device.getSn(), door.getDoorIndex());
    }

    @Override
    @Transactional
    public void completeDelivery(DeliveryReportRequest request) {
        // 明文 SN 信任：按 SN 反查设备（sn 全局唯一；IoT 链路无租户上下文，拦截器放行全局查询）
        Device device = deviceService.lambdaQuery().eq(Device::getSn, request.getSn()).one();
        if (device == null) {
            throw new BusinessException(404, "设备未注册: " + request.getSn());
        }

        // 幂等：按 device + msgId（OneNet 消息 id / MQ messageId，落 delivery_token 列）去重，
        //   适配 MQ at-least-once 重投。msgId 为空（直连未带）则跳过去重。
        String msgId = request.getMsgId();
        if (msgId != null && !msgId.isBlank()) {
            DeliveryOrder exist = lambdaQuery()
                    .eq(DeliveryOrder::getDeviceId, device.getId())
                    .eq(DeliveryOrder::getDeliveryToken, msgId)
                    .one();
            if (exist != null) {
                log.info("[投递] 重复上报已忽略 sn={}, msgId={}", request.getSn(), msgId);
                return;
            }
        }

        // 按 device + doorIndex 反查投口（取单价、分类兜底）
        Door door = doorService.lambdaQuery()
                .eq(Door::getDeviceId, device.getId())
                .eq(Door::getDoorIndex, request.getDoorIndex())
                .one();

        // 取该设备「当前活跃用户」会话确定归属；无/过期 → 无主单
        DeviceSession session = deviceSessionService.findActive(device.getId());

        DeliveryOrder order = new DeliveryOrder();
        order.setDeviceId(device.getId());
        order.setDoorId(door != null ? door.getId() : null);
        order.setDeliveryToken(request.getMsgId());   // 幂等键落库（device+msgId 唯一）
        order.setWeight(request.getWeight());
        order.setStatus(0);
        order.setDeliveryStatus(1);     // 上传即完成
        // 分类：上报优先，否则取投口配置兜底
        order.setWasteType1(request.getWasteType1() != null ? request.getWasteType1()
                : (door != null ? door.getWasteType1() : 0));
        order.setWasteType2(request.getWasteType2() != null ? request.getWasteType2()
                : (door != null && door.getWasteType2() != null ? door.getWasteType2() : 0));
        // 照片 URL：设备直传 COS 后随本次称重上报回传，后端原样存（继续投递时位置由设备定，不由后端复原）
        order.setPhotoOpenOutside(request.getPhotoOpenOutside());
        order.setPhotoOpenInside(request.getPhotoOpenInside());
        order.setPhotoCloseOutside(request.getPhotoCloseOutside());
        order.setPhotoCloseInside(request.getPhotoCloseInside());

        if (session != null) {
            order.setTenantId(session.getTenantId());
            order.setUserId(session.getUserId());
            order.setLoginType(session.getLoginType());
        } else {
            // 无主单：归设备租户、不绑用户、不返现，告警待认领
            order.setTenantId(device.getTenantId());
            log.warn("[投递] 无活跃用户会话，建无主单 sn={}, doorIndex={}",
                    request.getSn(), request.getDoorIndex());
        }

        // 计算返现单价（有投口配置时回填）
        BigDecimal amount = null;
        if (door != null && door.getPrice() != null && request.getWeight() != null) {
            order.setPrice(door.getPrice());
            amount = door.getPrice().multiply(request.getWeight()).setScale(2, RoundingMode.HALF_UP);
        }
        save(order);

        // 仅命中活跃用户时入账并续期会话；无主单不返现
        if (session != null && amount != null) {
            walletService.income(order.getUserId(), order.getTenantId(), amount, order.getId());
            deviceSessionService.refresh(device.getId());
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
