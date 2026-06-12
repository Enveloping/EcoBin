package org.enveloping.ecobin.device.entity;

import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;
import org.enveloping.ecobin.common.base.BaseEntity;

import java.time.LocalDateTime;

/**
 * 设备活跃会话：一台设备一行「当前活跃用户」，用于「上传后建单」时把设备上报关联到用户。
 * <p>
 * 用户小程序「开启设备」时按 {@code device_id} upsert（覆盖上一行，接受「最近用户」语义）；
 * 设备投递上报到达时按 {@code device_id} 查未过期会话以确定归属，过期/无则建无主单。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("biz_device_session")
public class DeviceSession extends BaseEntity {

    /** 设备ID（唯一，一台设备一行） */
    private Long deviceId;

    /** 当前活跃用户ID */
    private Long userId;

    /** 登录方式：1-手机 2-IC卡 3-人脸 4-二维码 */
    private Integer loginType;

    /** 会话过期时间（超过即视为无活跃用户） */
    private LocalDateTime expireTime;
}
