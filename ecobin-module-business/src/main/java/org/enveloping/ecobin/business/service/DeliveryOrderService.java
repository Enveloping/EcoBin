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

    /** 终端用户分页查询「自己的」投递记录（按 user_id 过滤，叠加租户拦截器的 tenant_id 隔离） */
    PageResult<DeliveryOrder> pageMyOrders(Long userId, int page, int pageSize);

    /** 终端用户查询「自己的」单条投递详情；不存在或不属于自己均抛业务异常 */
    DeliveryOrder getMyOrder(Long userId, Long id);
}
