package org.enveloping.ecobin.system.service;

import com.baomidou.mybatisplus.extension.service.IService;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.system.dto.UserPageQuery;
import org.enveloping.ecobin.system.entity.User;

/**
 * 用户服务接口（终端用户 CRUD；登录见 AuthService）
 */
public interface UserService extends IService<User> {

    /** 分页查询（使用 PageResult 格式） */
    PageResult<User> pageUsers(UserPageQuery query);

    /**
     * 租户提升/降低终端用户角色（仅限 1/2/3）。
     * 租户拦截器限定本租户范围，跨租户用户按"不存在"处理；改角色后旧 token 立即失效。
     */
    User changeRole(Long id, Integer role);
}
