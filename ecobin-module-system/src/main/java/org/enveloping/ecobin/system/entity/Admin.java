package org.enveloping.ecobin.system.entity;

import com.baomidou.mybatisplus.annotation.TableName;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;
import lombok.EqualsAndHashCode;
import org.enveloping.ecobin.common.base.PlatformBaseEntity;

/**
 * 平台管理员实体（sys_admin）。
 * <p>
 * 平台级登录主体，无 tenant_id / openid / phone / email。role = 9(超管) / 8(管理员)。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("sys_admin")
public class Admin extends PlatformBaseEntity {

    /** 登录用户名 */
    private String username;

    /** 加密密码（只写，不返回前端） */
    @JsonProperty(access = JsonProperty.Access.WRITE_ONLY)
    private String password;

    /** 真实姓名 */
    private String realName;

    /** 角色：9-超级管理员 8-管理员 */
    private Integer role;

    /** 状态：0-禁用 1-启用 */
    private Integer status;
}
