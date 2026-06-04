package org.enveloping.ecobin.system.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.enveloping.ecobin.system.entity.Admin;

/**
 * 平台管理员 Mapper
 */
@Mapper
public interface AdminMapper extends BaseMapper<Admin> {

    /** 按用户名查询（登录用） */
    @Select("SELECT * FROM sys_admin WHERE username = #{username}")
    Admin selectByUsername(@Param("username") String username);
}
