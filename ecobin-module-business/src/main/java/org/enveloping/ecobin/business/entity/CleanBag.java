package org.enveloping.ecobin.business.entity;

import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;
import org.enveloping.ecobin.common.base.BaseEntity;

import java.math.BigDecimal;

/**
 * 垃圾袋追踪实体。
 * <p>
 * 每个设备投口维护"当前那只袋"的去皮重量与编号：每次清运换上新空袋时由设备上报去皮重量并 upsert，
 * 下一次清运时设备上报的毛重减去此去皮即本周期实际清运量。唯一键 {@code (device_id, door_index)}。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("biz_clean_bag")
public class CleanBag extends BaseEntity {

    /** 设备ID */
    private Long deviceId;

    /** 投口号（物理编号，第几个投口） */
    private Integer doorIndex;

    /** 当前垃圾袋二维码/编号 */
    private String bagQr;

    /** 当前垃圾袋去皮重量（kg） */
    private BigDecimal tareWeight;

    /** 最近换袋清运人ID */
    private Long userId;
}
