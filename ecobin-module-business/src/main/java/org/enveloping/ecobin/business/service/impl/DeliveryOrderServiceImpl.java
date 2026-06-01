package org.enveloping.ecobin.business.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import org.enveloping.ecobin.business.entity.DeliveryOrder;
import org.enveloping.ecobin.business.mapper.DeliveryOrderMapper;
import org.enveloping.ecobin.business.service.DeliveryOrderService;
import org.enveloping.ecobin.common.constant.Constants;
import org.enveloping.ecobin.common.result.PageResult;
import org.springframework.stereotype.Service;

import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

@Service
public class DeliveryOrderServiceImpl extends ServiceImpl<DeliveryOrderMapper, DeliveryOrder> implements DeliveryOrderService {

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
    public PageResult<DeliveryOrder> pageOrders(int page, int pageSize) {
        Page<DeliveryOrder> p = new Page<>(page, pageSize);
        Page<DeliveryOrder> result = page(p, new LambdaQueryWrapper<DeliveryOrder>().orderByDesc(DeliveryOrder::getCreateTime));
        return PageResult.of(result.getRecords(), result.getTotal(), page, pageSize);
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
