package org.enveloping.ecobin.system.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.enveloping.ecobin.system.entity.User;

/**
 * 用户 Mapper（仅保留 BaseMapper 无法覆盖的自定义查询）
 */
@Mapper
public interface UserMapper extends BaseMapper<User> {

    /** 按用户名查询（登录用） */
    @Select("SELECT * FROM sys_user WHERE username = #{username}")
    User selectByUsername(@Param("username") String username);
}
