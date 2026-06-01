package org.enveloping.ecobin.system.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.common.constant.Constants;
import org.enveloping.ecobin.common.exception.BusinessException;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.framework.security.JwtTokenProvider;
import org.enveloping.ecobin.framework.wechat.WechatMiniappClient;
import org.enveloping.ecobin.framework.wechat.WechatSessionResponse;
import org.enveloping.ecobin.system.dto.LoginRequest;
import org.enveloping.ecobin.system.dto.LoginResponse;
import org.enveloping.ecobin.system.dto.UserPageQuery;
import org.enveloping.ecobin.system.dto.WxLoginRequest;
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
    private final WechatMiniappClient wechatMiniappClient;

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
                .nickname(user.getNickname())
                .avatar(user.getAvatar())
                .build();
    }

    @Override
    public LoginResponse wxLogin(WxLoginRequest request) {
        // 1. 调用微信 API 换取 openid
        WechatSessionResponse session = wechatMiniappClient.code2session(request.getCode());

        if (session.getOpenid() == null || session.getOpenid().isEmpty()) {
            throw new BusinessException(500, "获取微信 openid 失败");
        }

        // 2. 查找是否已有该 openid 绑定的用户
        User user = userMapper.selectByOpenid(session.getOpenid());

        if (user == null) {
            // 3. 自动注册新用户
            user = new User();
            user.setOpenid(session.getOpenid());
            user.setUnionid(session.getUnionid());
            user.setNickname(Constants.WECHAT_NICKNAME_PREFIX + System.currentTimeMillis() % 100000);
            user.setRole(Constants.WECHAT_DEFAULT_ROLE);
            user.setStatus(1);
            user.setTenantId(Constants.DEFAULT_TENANT_ID);
            save(user);
        }

        // 4. 检查用户状态
        if (user.getStatus() != null && user.getStatus() == 0) {
            throw new BusinessException(403, "账号已被禁用");
        }

        // 5. 生成 JWT（使用 openid 作为 subject，因为 username 可能为空）
        Long tenantId = user.getTenantId() != null ? user.getTenantId() : Constants.DEFAULT_TENANT_ID;
        String token = jwtTokenProvider.generateToken(user.getId(), user.getOpenid(), tenantId);

        return LoginResponse.builder()
                .token(token)
                .userId(user.getId())
                .username(user.getUsername())
                .realName(user.getRealName())
                .role(user.getRole())
                .nickname(user.getNickname())
                .avatar(user.getAvatar())
                .build();
    }

    @Override
    public PageResult<User> pageUsers(UserPageQuery query) {
        Page<User> page = new Page<>(query.getPage(), query.getPageSize());
        Page<User> result = page(page, new LambdaQueryWrapper<User>().orderByAsc(User::getId));
        return PageResult.of(result.getRecords(), result.getTotal(), query.getPage(), query.getPageSize());
    }
}
