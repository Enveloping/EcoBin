package org.enveloping.ecobin.device.entity;

import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;
import org.enveloping.ecobin.common.base.BaseEntity;

import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * 设备实时状态（设备级——配置 / 物理 / 健康）。
 * <p>
 * V10 重整：去 totalWeight/spillAlarm/smokeAlarm（迁往 biz_door_status），加 rssi/fwVersion。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("biz_device_status")
public class DeviceStatus extends BaseEntity {

    /** 设备ID */
    private Long deviceId;

    /** 在线状态：0-离线 1-在线 */
    private Integer online;

    /** 电压（V） */
    private BigDecimal voltage;

    /** 信号强度（dBm，V10 新增） */
    private Integer rssi;

    /** 固件版本（V10 新增） */
    private String fwVersion;

    /** 最后上报时间 */
    private LocalDateTime lastReportTime;
}
