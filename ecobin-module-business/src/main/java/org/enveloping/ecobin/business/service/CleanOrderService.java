package org.enveloping.ecobin.business.service;

import com.baomidou.mybatisplus.extension.service.IService;
import org.enveloping.ecobin.business.dto.CleanSubmitRequest;
import org.enveloping.ecobin.business.entity.CleanOrder;
import org.enveloping.ecobin.common.result.PageResult;

public interface CleanOrderService extends IService<CleanOrder> {

    /** 分页查询 */
    PageResult<CleanOrder> pageOrders(int page, int pageSize);

    /** 审核清运订单 */
    CleanOrder audit(Long id, Integer auditStatus);

    /** C 端清运员提交清运（userId 取登录态，不信任 body） */
    CleanOrder submitClean(Long userId, CleanSubmitRequest request);

    /** 我的清运记录分页（按 user_id 归属过滤） */
    PageResult<CleanOrder> pageMyOrders(Long userId, int page, int pageSize);

    /** 我的单条清运详情（归属校验，越权/不存在统一按不存在处理） */
    CleanOrder getMyOrder(Long userId, Long id);
}
