package org.enveloping.ecobin.system.entity;

import com.baomidou.mybatisplus.annotation.TableName;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;
import lombok.EqualsAndHashCode;
import org.enveloping.ecobin.common.base.PlatformBaseEntity;

/**
 * 租户/机构实体（sys_tenant）。
 * <p>
 * 租户自身即数据隔离空间（{@code id} 即各业务表的 tenant_id），故本表无 tenant_id 列，
 * 改继承 {@link PlatformBaseEntity}（不含 tenant_id）。同时是网页登录主体（role=7）。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("sys_tenant")
public class Tenant extends PlatformBaseEntity {

    /** 租户名称 */
    private String name;

    /** 租户编码 */
    private String code;

    /** 租户登录用户名 */
    private String username;

    /** 加密密码（只写，不返回前端） */
    @JsonProperty(access = JsonProperty.Access.WRITE_ONLY)
    private String password;

    /** 小程序 AppID（未配置存 NULL） */
    private String miniappAppid;

    /** 小程序 Secret（AES 加密存储，只写不返回前端） */
    @JsonProperty(access = JsonProperty.Access.WRITE_ONLY)
    private String miniappSecret;

    /** 微信商户号 */
    private String merchantNo;

    /** 联系人 */
    private String contactName;

    /** 联系电话 */
    private String contactPhone;

    /** 地址 */
    private String address;

    /** 状态：0-禁用 1-启用 */
    private Integer status;
}
