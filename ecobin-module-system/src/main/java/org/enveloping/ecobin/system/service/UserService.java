package org.enveloping.ecobin.system.service;

import com.baomidou.mybatisplus.extension.service.IService;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.system.dto.LoginRequest;
import org.enveloping.ecobin.system.dto.LoginResponse;
import org.enveloping.ecobin.system.dto.UserPageQuery;
import org.enveloping.ecobin.system.entity.User;

/**
 * 用户服务接口
 */
public interface UserService extends IService<User> {

    /** 登录 */
    LoginResponse login(LoginRequest request);

    /** 分页查询（使用 PageResult 格式） */
    PageResult<User> pageUsers(UserPageQuery query);
}
