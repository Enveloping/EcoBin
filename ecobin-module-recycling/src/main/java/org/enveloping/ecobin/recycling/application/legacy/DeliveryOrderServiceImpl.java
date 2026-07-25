package org.enveloping.ecobin.recycling.application.legacy;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.enveloping.ecobin.common.constant.Constants;
import org.enveloping.ecobin.common.exception.BusinessException;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.device.api.legacy.LegacyDeviceAccessPort;
import org.enveloping.ecobin.device.api.legacy.LegacyDeviceId;
import org.enveloping.ecobin.device.api.legacy.LegacyDeviceSessionSnapshot;
import org.enveloping.ecobin.device.api.legacy.LegacyDeviceSnapshot;
import org.enveloping.ecobin.device.api.legacy.LegacyDoorId;
import org.enveloping.ecobin.device.api.legacy.LegacyDoorSnapshot;
import org.enveloping.ecobin.framework.security.SecurityUtils;
import org.enveloping.ecobin.funds.api.legacy.LegacyWalletCreditPort;
import org.enveloping.ecobin.identity.api.legacy.LegacyOrganizationUserId;
import org.enveloping.ecobin.recycling.api.legacy.LegacyDeliveryEventPort;
import org.enveloping.ecobin.recycling.api.legacy.LegacyDeliveryReportCommand;
import org.enveloping.ecobin.recycling.api.legacy.LegacyDeliveryStatistics;
import org.enveloping.ecobin.recycling.api.legacy.LegacyDeliveryStatisticsPort;
import org.enveloping.ecobin.recycling.api.legacy.LegacyDeviceDeliveryRanking;
import org.enveloping.ecobin.recycling.domain.legacy.DeliveryOrder;
import org.enveloping.ecobin.recycling.infrastructure.persistence.mapper.DeliveryOrderMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

@Slf4j
@Service
@RequiredArgsConstructor
public class DeliveryOrderServiceImpl extends ServiceImpl<DeliveryOrderMapper, DeliveryOrder>
        implements DeliveryOrderService, LegacyDeliveryEventPort, LegacyDeliveryStatisticsPort {

    private final LegacyDeviceAccessPort deviceAccessPort;
    private final LegacyWalletCreditPort walletCreditPort;

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

        // 不建单：仅记「设备当前活跃用户」会话（loginType=4 二维码扫码），建单移到设备上传之后。
        deviceAccessPort.activateSession(device.id(), door.tenantId(), userId, 4);

        // 下发开投口指令（仅含 COS 凭证；token/照片 key 由设备每次开门自生成）
        deviceAccessPort.openDeliveryDoor(device.sn(), door.doorIndex());
    }

    @Override
    @Transactional
    public void completeDelivery(LegacyDeliveryReportCommand command) {
        // 明文 SN 信任：按 SN 反查设备（sn 全局唯一；IoT 链路无租户上下文，拦截器放行全局查询）
        LegacyDeviceSnapshot device = deviceAccessPort.findDeviceBySn(command.sn());
        if (device == null) {
            throw new BusinessException(404, "设备未注册: " + command.sn());
        }

        // 幂等：按 device + msgId（OneNet 消息 id / MQ messageId，落 delivery_token 列）去重，
        //   适配 MQ at-least-once 重投。msgId 为空（直连未带）则跳过去重。
        String msgId = command.messageId();
        if (msgId != null && !msgId.isBlank()) {
            DeliveryOrder exist = lambdaQuery()
                    .eq(DeliveryOrder::getDeviceId, device.id().value())
                    .eq(DeliveryOrder::getDeliveryToken, msgId)
                    .one();
            if (exist != null) {
                log.info("[投递] 重复上报已忽略 sn={}, msgId={}", command.sn(), msgId);
                return;
            }
        }

        // 按 device + doorIndex 反查投口（取单价、分类兜底）
        LegacyDoorSnapshot door = deviceAccessPort.findDoor(device.id(), command.doorIndex());

        // 取该设备「当前活跃用户」会话确定归属；无/过期 → 无主单
        LegacyDeviceSessionSnapshot session = deviceAccessPort.findActiveSession(device.id());

        DeliveryOrder order = new DeliveryOrder();
        order.setDeviceId(device.id().value());
        order.setDoorId(door != null ? door.id().value() : null);
        order.setDeliveryToken(command.messageId());   // 幂等键落库（device+msgId 唯一）
        order.setWeight(command.weight());
        order.setStatus(0);
        order.setDeliveryStatus(1);     // 上传即完成
        order.setAuditStatus(0);        // 待审核：返现入账迁移到审核通过时
        // 分类取投口配置（开门/上报不再传分类）
        order.setWasteType1(door != null ? door.wasteType1() : 0);
        order.setWasteType2(door != null && door.wasteType2() != null ? door.wasteType2() : 0);
        // 照片 URL：设备直传 COS 后随本次称重上报回传，后端原样存（继续投递时位置由设备定，不由后端复原）
        order.setPhotoOpenOutside(command.photoOpenOutside());
        order.setPhotoOpenInside(command.photoOpenInside());
        order.setPhotoCloseOutside(command.photoCloseOutside());
        order.setPhotoCloseInside(command.photoCloseInside());

        if (session != null) {
            order.setTenantId(session.tenantId());
            order.setUserId(session.userId());
            order.setLoginType(session.loginType());
        } else {
            // 无主单：归设备租户、不绑用户、不返现，告警待认领
            order.setTenantId(device.tenantId());
            log.warn("[投递] 无活跃用户会话，建无主单 sn={}, doorIndex={}",
                    command.sn(), command.doorIndex());
        }

        // 回填返现单价（有投口配置时）；返现金额在审核通过时按 price×weight 重算入账
        if (door != null && door.price() != null && command.weight() != null) {
            order.setPrice(door.price());
        }
        save(order);

        // 命中活跃用户即续期会话（与入账解耦）；无主单不续期
        if (session != null) {
            deviceAccessPort.refreshSession(device.id());
        }
    }

    @Override
    @Transactional
    public void audit(Long id, Integer auditStatus, String remark) {
        DeliveryOrder order = getById(id);
        if (order == null) {
            throw new BusinessException(404, "投递订单不存在");
        }
        if (auditStatus == null || (auditStatus != 1 && auditStatus != 2)) {
            throw new BusinessException(400, "审核状态无效（1=通过 2=拒绝）");
        }
        // 幂等：仅允许「待审核」流转，杜绝重复入账
        if (order.getAuditStatus() != null && order.getAuditStatus() != 0) {
            throw new BusinessException(400, "订单已审核");
        }
        // 审核通过且有归属用户、有单价重量：按 price×weight 返现入账
        if (auditStatus == 1 && order.getUserId() != null
                && order.getPrice() != null && order.getWeight() != null) {
            BigDecimal amount = order.getPrice().multiply(order.getWeight()).setScale(2, RoundingMode.HALF_UP);
            walletCreditPort.credit(
                    new LegacyOrganizationUserId(order.getUserId()),
                    order.getTenantId(),
                    amount,
                    order.getId());
        }
        order.setAuditStatus(auditStatus);
        order.setAuditTime(java.time.LocalDateTime.now());
        order.setAuditRemark(remark);
        updateById(order);
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

    @Override
    public LegacyDeliveryStatistics statistics() {
        return new LegacyDeliveryStatistics(
                baseMapper.countToday(),
                toDouble(baseMapper.sumTodayWeight()),
                baseMapper.countTodayMembers(),
                baseMapper.countMonth(),
                toDouble(baseMapper.sumMonthWeight()),
                toDouble(baseMapper.sumMonthMoney()));
    }

    @Override
    public List<LegacyDeviceDeliveryRanking> deviceRanking(int pageSize) {
        List<LegacyDeviceDeliveryRanking> result = new ArrayList<>();
        for (Map<String, Object> row : baseMapper.deviceRanking(pageSize)) {
            Number deviceId = (Number) value(row, "device_id");
            Object totalWeight = value(row, "total_weight");
            if (deviceId == null || totalWeight == null) {
                continue;
            }
            Object deviceName = value(row, "device_name");
            result.add(new LegacyDeviceDeliveryRanking(
                    new LegacyDeviceId(deviceId.longValue()),
                    deviceName == null ? null : deviceName.toString(),
                    new BigDecimal(String.valueOf(totalWeight))));
        }
        return result;
    }

    private static Object value(Map<String, Object> row, String name) {
        return row.entrySet().stream()
                .filter(entry -> entry.getKey().equalsIgnoreCase(name))
                .map(Map.Entry::getValue)
                .findFirst()
                .orElse(null);
    }

    private static double toDouble(Double value) {
        return value == null ? 0.0 : value;
    }
}
