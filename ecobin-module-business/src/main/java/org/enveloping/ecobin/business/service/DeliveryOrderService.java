package org.enveloping.ecobin.business.service;

import com.baomidou.mybatisplus.extension.service.IService;
import org.enveloping.ecobin.business.dto.DeliveryReportRequest;
import org.enveloping.ecobin.business.entity.DeliveryOrder;
import org.enveloping.ecobin.common.result.PageResult;

import java.util.Map;

public interface DeliveryOrderService extends IService<DeliveryOrder> {

    /** 分页查询 */
    PageResult<DeliveryOrder> pageOrders(int page, int pageSize);

    /**
     * 投递·开启设备：C 端用户在小程序「开启设备」。
     * 仅写入该设备「当前活跃用户」会话并下发开投口指令，<strong>不建单</strong>
     * （建单移到设备上传称重数据之后，见 {@link #completeDelivery}）。
     *
     * @param doorId 投口ID（受当前登录用户的租户隔离约束）
     */
    void openDoor(Long doorId);

    /**
     * 投递·上传后建单：设备 IoT 上报投递完成（含 4 个照片 URL）。
     * 按设备 SN + doorIndex 定位投口，取该设备「当前活跃用户」会话确定归属并建单
     * （无活跃会话则建无主单、不返现）。按 device + {@code msgId}（OneNet 消息 id / MQ messageId）幂等去重。
     */
    void completeDelivery(DeliveryReportRequest request);

    /** 今日概览 */
    Map<String, Object> todayOverview();

    /** 终端用户分页查询「自己的」投递记录（按 user_id 过滤，叠加租户拦截器的 tenant_id 隔离） */
    PageResult<DeliveryOrder> pageMyOrders(Long userId, int page, int pageSize);

    /** 终端用户查询「自己的」单条投递详情；不存在或不属于自己均抛业务异常 */
    DeliveryOrder getMyOrder(Long userId, Long id);

    /**
     * 审核投递订单（网页后台，租户管理员/超管）。
     * 仅「待审核」可流转；通过（auditStatus=1）时按 price×weight 返现入账，拒绝（2）不入账。
     * 重复审核抛业务异常，杜绝重复入账。
     */
    void audit(Long id, Integer auditStatus, String remark);
}
