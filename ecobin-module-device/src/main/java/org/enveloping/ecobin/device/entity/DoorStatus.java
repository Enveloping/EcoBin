package org.enveloping.ecobin.device.entity;

import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;
import org.enveloping.ecobin.common.base.BaseEntity;

import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * 投口实时状态（投口级快照，V10 新增）。
 * <p>
 * 每个设备投口一条最新快照，承载重量/满溢/烟雾等实时数据。
 * 配置（启用/分类/单价）仍在 biz_door；当前垃圾袋号/去皮仍在 biz_clean_bag，本表不重复存。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("biz_door_status")
public class DoorStatus extends BaseEntity {

    /** 设备ID */
    private Long deviceId;

    /** 投口号（物理编号） */
    private Integer doorIndex;

    /** 当前即时重量（kg） */
    private BigDecimal weight;

    /** 满溢度（0-100） */
    private Integer fullness;

    /** 满溢报警：0-否 1-是 */
    private Integer spillAlarm;

    /** 烟雾报警：0-否 1-是 */
    private Integer smokeAlarm;

    /** 最后上报时间 */
    private LocalDateTime lastReportTime;
}
