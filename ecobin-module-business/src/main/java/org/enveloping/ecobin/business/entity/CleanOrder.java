package org.enveloping.ecobin.business.entity;

import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;
import org.enveloping.ecobin.common.base.BaseEntity;

import java.math.BigDecimal;

/**
 * 清运订单实体
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("biz_clean_order")
public class CleanOrder extends BaseEntity {

    /** 订单编号 */
    private String orderSn;

    /** 设备ID */
    private Long deviceId;

    /** 投口ID */
    private Long doorId;

    /** 本次清运清走的垃圾袋编号 */
    private String bagQr;

    /** 清运员ID */
    private Long userId;

    /** 一级分类 */
    private Integer wasteType1;

    /** 二级分类 */
    private Integer wasteType2;

    /** 清理重量（kg），等同 {@link #netWeight}，保留以兼容旧后台/统计查询 */
    private BigDecimal weight;

    /** 清运毛重（kg，设备上报的满袋重量） */
    private BigDecimal grossWeight;

    /** 去皮重量（kg，清运时该投口当前垃圾袋去皮） */
    private BigDecimal tareWeight;

    /** 实际清运量（kg）= 毛重 - 去皮 */
    private BigDecimal netWeight;

    /**
     * 审核状态：0-待审核 1-审核通过 2-审核拒绝。
     * @deprecated 清运改为设备自动称重上报后，人工审核流程已废弃；新记录默认置 1。
     */
    @Deprecated
    private Integer auditStatus;

    /** 订单状态：0-创建 1-完成 2-取消 */
    private Integer status;
}
