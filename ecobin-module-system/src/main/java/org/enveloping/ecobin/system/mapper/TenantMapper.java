package org.enveloping.ecobin.system.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.enveloping.ecobin.system.entity.Tenant;

/**
 * 租户 Mapper
 */
@Mapper
public interface TenantMapper extends BaseMapper<Tenant> {

    /** 按登录用户名查询（租户网页登录用） */
    @Select("SELECT * FROM sys_tenant WHERE username = #{username}")
    Tenant selectByUsername(@Param("username") String username);

    /** 按小程序 AppID 查询（wx-login 定位租户用） */
    @Select("SELECT * FROM sys_tenant WHERE miniapp_appid = #{appid}")
    Tenant selectByMiniappAppid(@Param("appid") String appid);
}
