package org.enveloping.ecobin.business.service;

import com.baomidou.mybatisplus.extension.service.IService;
import org.enveloping.ecobin.business.entity.DeliveryOrder;
import org.enveloping.ecobin.common.result.PageResult;

import java.util.Map;

public interface DeliveryOrderService extends IService<DeliveryOrder> {

    /** 分页查询 */
    PageResult<DeliveryOrder> pageOrders(int page, int pageSize);

    /** 今日概览 */
    Map<String, Object> todayOverview();
}
