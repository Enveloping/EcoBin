package org.enveloping.ecobin.business.service;

import com.baomidou.mybatisplus.extension.service.IService;
import org.enveloping.ecobin.business.dto.DeliveryReportRequest;
import org.enveloping.ecobin.business.dto.OpenDoorResult;
import org.enveloping.ecobin.business.entity.DeliveryOrder;
import org.enveloping.ecobin.common.result.PageResult;

import java.util.Map;

public interface DeliveryOrderService extends IService<DeliveryOrder> {

    /** 分页查询 */
    PageResult<DeliveryOrder> pageOrders(int page, int pageSize);

    /**
     * 投递两阶段·阶段1：C 端用户开投口。
     * 创建一条「进行中」投递记录（含 user_id 与新生成的投递标识符），并下发开投口指令。
     *
     * @param doorId 投口ID（受当前登录用户的租户隔离约束）
     * @return 投递记录ID与投递标识符
     */
    OpenDoorResult openDoor(Long doorId);

    /**
     * 投递两阶段·阶段2：设备 IoT 上报投递完成。
     * 按设备 SN + 投递标识符关联「进行中」记录，回填重量并置为已完成。
     */
    void completeDelivery(DeliveryReportRequest request);

    /** 今日概览 */
    Map<String, Object> todayOverview();

    /** 终端用户分页查询「自己的」投递记录（按 user_id 过滤，叠加租户拦截器的 tenant_id 隔离） */
    PageResult<DeliveryOrder> pageMyOrders(Long userId, int page, int pageSize);

    /** 终端用户查询「自己的」单条投递详情；不存在或不属于自己均抛业务异常 */
    DeliveryOrder getMyOrder(Long userId, Long id);
}
