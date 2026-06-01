package org.enveloping.ecobin.business.entity;

import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;
import org.enveloping.ecobin.common.base.BaseEntity;

import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * 投递订单实体
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("biz_delivery_order")
public class DeliveryOrder extends BaseEntity {

    /** 订单编号 */
    private String orderSn;

    /** 设备ID */
    private Long deviceId;

    /** 投口ID */
    private Long doorId;

    /** 投递用户ID */
    private Long userId;

    /** 一级分类 */
    private Integer wasteType1;

    /** 二级分类 */
    private Integer wasteType2;

    /** 重量（kg） */
    private BigDecimal weight;

    /** 单价 */
    private BigDecimal price;

    /** 获得积分 */
    private Integer score;

    /** 登录方式：1-手机 2-IC卡 3-人脸 4-二维码 */
    private Integer loginType;

    /** 状态：0-正常 -1-异常 */
    private Integer status;

    /** 投递时间（使用 createTime） */
    public LocalDateTime getDeliveryTime() {
        return getCreateTime();
    }
}
