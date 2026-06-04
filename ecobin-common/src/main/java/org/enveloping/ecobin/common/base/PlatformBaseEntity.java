package org.enveloping.ecobin.common.base;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import lombok.Data;

import java.time.LocalDateTime;

/**
 * 平台级实体基类：不含 {@code tenant_id}。
 * <p>
 * 用于自身不归属任何租户的登录主体表（如 {@code sys_admin}、{@code sys_tenant}）。
 * 业务/用户实体仍继承 {@link BaseEntity}（含 tenant_id）。
 */
@Data
public abstract class PlatformBaseEntity {

    /** 主键 */
    @TableId(type = IdType.AUTO)
    private Long id;

    /** 创建时间 */
    private LocalDateTime createTime;

    /** 更新时间 */
    private LocalDateTime updateTime;
}
