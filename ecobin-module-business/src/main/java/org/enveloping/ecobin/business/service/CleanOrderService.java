package org.enveloping.ecobin.business.service;

import com.baomidou.mybatisplus.extension.service.IService;
import org.enveloping.ecobin.business.dto.CleanGrossRequest;
import org.enveloping.ecobin.business.dto.CleanTareRequest;
import org.enveloping.ecobin.business.entity.CleanOrder;
import org.enveloping.ecobin.common.result.PageResult;

public interface CleanOrderService extends IService<CleanOrder> {

    /** 分页查询（后台） */
    PageResult<CleanOrder> pageOrders(int page, int pageSize);

    /**
     * C 端清运员开清运门：扫新空垃圾袋后**即建清运订单**（登录态 userId + 新袋编号），校验投口/设备归属，
     * 下发开清运门指令（携带 doorIndex + cleanOrderId），返回新建订单。
     *
     * @param doorId 投口ID（本租户）
     * @param bagNo  本次清运换上的新垃圾袋编号（记在订单 newBagQr，待去皮上报补重）
     * @return 新建的清运订单（含 id，供小程序展示）
     */
    CleanOrder openCleanDoor(Long doorId, String bagNo);

    /**
     * 设备 IoT 上报清运毛重（图④）：按 {@code cleanOrderId} 回填订单，{@code net = 毛重 - 该投口当前(旧袋)去皮}。
     * {@code cleanOrderId} 充当幂等键（已回填毛重则直接返回）。明文 SN 信任，校验订单归属。
     */
    CleanOrder reportGross(CleanGrossRequest request);

    /**
     * 设备 IoT 上报换新空袋去皮（图⑤）：按 {@code cleanOrderId} 取订单的新袋编号，upsert 该投口当前垃圾袋去皮重量。
     * 明文 SN 信任，校验订单归属。
     */
    void reportTare(CleanTareRequest request);

    /** 我的清运记录分页（按 user_id 归属过滤） */
    PageResult<CleanOrder> pageMyOrders(Long userId, int page, int pageSize);

    /** 审核清运单：更新审核状态 */
    void audit(Long id, Integer auditStatus);

    /** 我的单条清运详情（归属校验，越权/不存在统一按不存在处理） */
    CleanOrder getMyOrder(Long userId, Long id);
}
