package org.enveloping.ecobin.device.entity;

import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;
import org.enveloping.ecobin.common.base.BaseEntity;

import java.math.BigDecimal;

/**
 * 设备实体
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("biz_device")
public class Device extends BaseEntity {

    /** 设备序列号 */
    private String sn;

    /** 设备名称 */
    private String name;

    /** 设备类型：1-智能垃圾箱 2-滚动系统 */
    private Integer type;

    /** 纬度 */
    private BigDecimal lat;

    /** 经度 */
    private BigDecimal lng;

    /** 安装地址 */
    private String address;

    /** 状态：0-离线 1-在线 2-维护中 */
    private Integer status;
}
