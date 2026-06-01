package org.enveloping.ecobin.business.service;

import com.baomidou.mybatisplus.extension.service.IService;
import org.enveloping.ecobin.business.entity.CleanOrder;
import org.enveloping.ecobin.common.result.PageResult;

public interface CleanOrderService extends IService<CleanOrder> {

    /** 分页查询 */
    PageResult<CleanOrder> pageOrders(int page, int pageSize);

    /** 审核清运订单 */
    CleanOrder audit(Long id, Integer auditStatus);
}
