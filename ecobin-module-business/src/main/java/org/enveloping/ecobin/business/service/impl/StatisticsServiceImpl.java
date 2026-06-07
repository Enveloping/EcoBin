package org.enveloping.ecobin.business.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.business.mapper.CleanOrderMapper;
import org.enveloping.ecobin.business.mapper.DeliveryOrderMapper;
import org.enveloping.ecobin.business.service.StatisticsService;
import org.enveloping.ecobin.common.constant.Constants;
import org.enveloping.ecobin.device.entity.Device;
import org.enveloping.ecobin.device.mapper.DeviceMapper;
import org.enveloping.ecobin.system.mapper.UserMapper;
import org.enveloping.ecobin.business.mapper.WithdrawOrderMapper;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@Service
@RequiredArgsConstructor
public class StatisticsServiceImpl implements StatisticsService {

    private final DeliveryOrderMapper deliveryOrderMapper;
    private final CleanOrderMapper cleanOrderMapper;
    private final UserMapper userMapper;
    private final WithdrawOrderMapper withdrawOrderMapper;
    private final DeviceMapper deviceMapper;

    @Override
    public Map<String, Object> dashboard() {
        Map<String, Object> result = new HashMap<>();
        result.put("deliveryCount", deliveryOrderMapper.countToday());
        result.put("totalWeight", toDouble(deliveryOrderMapper.sumTodayWeight()));
        result.put("todayMemberCount", deliveryOrderMapper.countTodayMembers());
        return result;
    }

    @Override
    public Map<String, Object> deviceStats() {
        long totalCount = deviceMapper.selectCount(new LambdaQueryWrapper<>());
        Map<String, Object> result = new HashMap<>();
        result.put("totalCount", totalCount);
        result.put("onlinkCount", 0);       // 待 biz_device_status 数据接入
        result.put("spillCount", 0);        // 待 biz_device_status 数据接入
        result.put("smokeCount", 0);        // 待 biz_device_status 数据接入
        return result;
    }

    @Override
    public Map<String, Object> memberStats() {
        Map<String, Object> result = new HashMap<>();
        result.put("memberCount", userMapper.countMembers());
        result.put("todayMemberCount", userMapper.countTodayMembers());
        result.put("memberDisableCount", userMapper.countDisabledMembers());
        return result;
    }

    @Override
    public Map<String, Object> deliveryStats() {
        Map<String, Object> result = new HashMap<>();
        result.put("deliveryCount", deliveryOrderMapper.countMonth());
        result.put("deliveryWeight", toDouble(deliveryOrderMapper.sumMonthWeight()));
        result.put("deliveryMoney", toDouble(deliveryOrderMapper.sumMonthMoney()));
        result.put("minusyMoney", 0);       // 当前无"审核异常金额"概念，预留
        return result;
    }

    @Override
    public Map<String, Object> cleanStats() {
        Map<String, Object> result = new HashMap<>();
        result.put("cleanCount", cleanOrderMapper.countMonth());
        result.put("totalWeights", toDouble(cleanOrderMapper.sumMonthWeight()));
        result.put("storageWeights", 0);    // 当前无"入库重量"概念，预留
        result.put("minusyWeights", 0);     // 当前无"异常重量"概念，预留
        return result;
    }

    @Override
    public Map<String, Object> payoutStats() {
        Map<String, Object> result = new HashMap<>();
        result.put("payOutCount", withdrawOrderMapper.countAll());
        result.put("payOutMoney", toDouble(withdrawOrderMapper.sumAmount()));
        result.put("pushSuccessMoney", toDouble(withdrawOrderMapper.sumApprovedAmount()));
        result.put("refundUserMoney", 0);   // 当前驳回即退回余额，无独立"退款"概念
        return result;
    }

    @Override
    public Map<String, Object> memberMoneyStats() {
        Map<String, Object> result = new HashMap<>();
        result.put("memberCount", userMapper.countMembers());
        result.put("memberMoney", toDouble(userMapper.sumBalance()));
        result.put("memberPlanMoney", toDouble(userMapper.sumPendingBalance()));
        result.put("memberScore", 0);       // 当前无积分体系
        return result;
    }

    @Override
    public List<Map<String, Object>> devicesMap() {
        List<Device> devices = deviceMapper.selectList(
                new LambdaQueryWrapper<Device>()
                        .isNotNull(Device::getLat)
                        .isNotNull(Device::getLng));
        return devices.stream().map(d -> {
            Map<String, Object> m = new HashMap<>();
            m.put("id", d.getId());
            m.put("sn", d.getSn());
            m.put("name", d.getName());
            m.put("lat", d.getLat());
            m.put("lng", d.getLng());
            //TODO 待 biz_device_status 数据接入
            m.put("onLink", 0);        // 待 biz_device_status 数据接入
            m.put("spillNow", 0);      // 待 biz_device_status 数据接入
            m.put("smoke", 0);         // 待 biz_device_status 数据接入
            return m;
        }).collect(Collectors.toList());
    }

    @Override
    public List<Map<String, Object>> deviceRanking(int pageSize) {
        if (pageSize <= 0) pageSize = 5;
        // 裸 SQL LIMIT 不经分页拦截器，单独 clamp 上限
        if (pageSize > Constants.MAX_PAGE_SIZE) pageSize = Constants.MAX_PAGE_SIZE;
        return deliveryOrderMapper.deviceRanking(pageSize);
    }

    /** null 安全的 Double → primitive（避免 unbox NPE） */
    private static double toDouble(Double v) {
        return v != null ? v : 0.0;
    }

    /** null 安全的 BigDecimal → primitive */
    private static double toDouble(BigDecimal v) {
        return v != null ? v.doubleValue() : 0.0;
    }
}
