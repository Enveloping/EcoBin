package org.enveloping.ecobin.system.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.common.constant.Constants;
import org.enveloping.ecobin.common.exception.BusinessException;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.framework.security.TokenInvalidationRegistry;
import org.enveloping.ecobin.system.dto.UserPageQuery;
import org.enveloping.ecobin.system.entity.User;
import org.enveloping.ecobin.system.mapper.UserMapper;
import org.enveloping.ecobin.system.service.UserService;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class UserServiceImpl extends ServiceImpl<UserMapper, User> implements UserService {

    private final UserMapper userMapper;
    private final PasswordEncoder passwordEncoder;
    private final TokenInvalidationRegistry tokenInvalidationRegistry;

    @Override
    public boolean save(User user) {
        // 检查用户名唯一性（仅当提供了用户名时）
        if (user.getUsername() != null) {
            User existing = userMapper.selectByUsername(user.getUsername());
            if (existing != null) {
                throw new BusinessException(400, "用户名已存在: " + user.getUsername());
            }
        }
        // 加密密码（仅当提供了密码时，微信用户无密码）
        if (user.getPassword() != null) {
            user.setPassword(passwordEncoder.encode(user.getPassword()));
        }
        if (user.getTenantId() == null) {
            user.setTenantId(Constants.DEFAULT_TENANT_ID);
        }
        return super.save(user);
    }

    @Override
    public boolean updateById(User user) {
        // 如果传了密码则加密
        if (user.getPassword() != null) {
            user.setPassword(passwordEncoder.encode(user.getPassword()));
        }
        boolean ok = super.updateById(user);
        // 角色或状态变更 → 强制该用户重新登录
        if (ok && user.getId() != null && (user.getRole() != null || user.getStatus() != null)) {
            tokenInvalidationRegistry.invalidateUser(user.getId());
        }
        return ok;
    }

    @Override
    public PageResult<User> pageUsers(UserPageQuery query) {
        Page<User> page = new Page<>(query.getPage(), query.getPageSize());
        Page<User> result = page(page, new LambdaQueryWrapper<User>().orderByAsc(User::getId));
        return PageResult.of(result.getRecords(), result.getTotal(), query.getPage(), query.getPageSize());
    }
}
