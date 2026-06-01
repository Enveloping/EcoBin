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

    /** 清运员ID */
    private Long userId;

    /** 一级分类 */
    private Integer wasteType1;

    /** 二级分类 */
    private Integer wasteType2;

    /** 清理重量（kg） */
    private BigDecimal weight;

    /** 审核状态：0-待审核 1-审核通过 2-审核拒绝 */
    private Integer auditStatus;

    /** 订单状态：0-创建 1-完成 2-取消 */
    private Integer status;
}
