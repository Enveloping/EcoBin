package org.enveloping.ecobin.device.entity;

import com.baomidou.mybatisplus.annotation.TableName;
import jakarta.validation.constraints.NotNull;
import lombok.Data;
import lombok.EqualsAndHashCode;
import org.enveloping.ecobin.common.base.BaseEntity;

/**
 * 投口实体
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("biz_door")
public class Door extends BaseEntity {

    /** 所属设备ID */
    @NotNull(message = "所属设备ID不能为空")
    private Long deviceId;

    /** 投口号（1-6） */
    @NotNull(message = "投口号不能为空")
    private Integer doorIndex;

    /** 投口名称 */
    private String name;

    /** 一级分类 */
    @NotNull(message = "一级分类不能为空")
    private Integer wasteType1;

    /** 二级分类 */
    private Integer wasteType2;

    /** 是否启用：0-禁用 1-启用 */
    private Integer enabled;

    /** 排序 */
    private Integer sortOrder;
}
