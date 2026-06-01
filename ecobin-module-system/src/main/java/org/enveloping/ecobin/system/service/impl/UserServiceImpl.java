package org.enveloping.ecobin.system.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.common.constant.Constants;
import org.enveloping.ecobin.common.exception.BusinessException;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.framework.security.JwtTokenProvider;
import org.enveloping.ecobin.system.dto.LoginRequest;
import org.enveloping.ecobin.system.dto.LoginResponse;
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
    private final JwtTokenProvider jwtTokenProvider;

    @Override
    public boolean save(User user) {
        // 检查用户名唯一性
        User existing = userMapper.selectByUsername(user.getUsername());
        if (existing != null) {
            throw new BusinessException(400, "用户名已存在: " + user.getUsername());
        }
        // 加密密码
        user.setPassword(passwordEncoder.encode(user.getPassword()));
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
        return super.updateById(user);
    }

    @Override
    public LoginResponse login(LoginRequest request) {
        User user = userMapper.selectByUsername(request.getUsername());
        if (user == null) {
            throw new BusinessException(401, "用户名或密码错误");
        }
        if (user.getStatus() != null && user.getStatus() == 0) {
            throw new BusinessException(403, "账号已被禁用");
        }
        if (!passwordEncoder.matches(request.getPassword(), user.getPassword())) {
            throw new BusinessException(401, "用户名或密码错误");
        }

        Long tenantId = user.getTenantId() != null ? user.getTenantId() : Constants.DEFAULT_TENANT_ID;
        String token = jwtTokenProvider.generateToken(user.getId(), user.getUsername(), tenantId);

        return LoginResponse.builder()
                .token(token)
                .userId(user.getId())
                .username(user.getUsername())
                .realName(user.getRealName())
                .role(user.getRole())
                .build();
    }

    @Override
    public PageResult<User> pageUsers(UserPageQuery query) {
        Page<User> page = new Page<>(query.getPage(), query.getPageSize());
        Page<User> result = page(page, new LambdaQueryWrapper<User>().orderByAsc(User::getId));
        return PageResult.of(result.getRecords(), result.getTotal(), query.getPage(), query.getPageSize());
    }
}
