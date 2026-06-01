package org.enveloping.ecobin.system.entity;

import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.EqualsAndHashCode;
import org.enveloping.ecobin.common.base.BaseEntity;

/**
 * 系统用户实体
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("sys_user")
public class User extends BaseEntity {

    /** 用户名 */
    private String username;

    /** 加密密码（只写，不返回给前端） */
    @JsonProperty(access = JsonProperty.Access.WRITE_ONLY)
    private String password;

    /** 真实姓名 */
    private String realName;

    /** 手机号 */
    private String phone;

    /** 邮箱 */
    private String email;

    /** 角色：1-超级管理员 2-设备管理员 3-运营人员 4-清运员 5-普通用户 */
    private Integer role;

    /** 状态：0-禁用 1-启用 */
    private Integer status;
}
