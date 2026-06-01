package org.enveloping.ecobin.device.entity;

import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;
import org.enveloping.ecobin.common.base.BaseEntity;

import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * 设备实时状态
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("biz_device_status")
public class DeviceStatus extends BaseEntity {

    /** 设备ID */
    private Long deviceId;

    /** 在线状态：0-离线 1-在线 */
    private Integer online;

    /** 当前总重量 */
    private BigDecimal totalWeight;

    /** 满溢报警：0-否 1-是 */
    private Integer spillAlarm;

    /** 烟雾报警：0-否 1-是 */
    private Integer smokeAlarm;

    /** 电压 */
    private BigDecimal voltage;

    /** 最后上报时间 */
    private LocalDateTime lastReportTime;
}
