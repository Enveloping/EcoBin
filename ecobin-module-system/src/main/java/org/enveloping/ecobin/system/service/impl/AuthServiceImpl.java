package org.enveloping.ecobin.system.service.impl;

import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.common.constant.Constants;
import org.enveloping.ecobin.common.enums.UserRole;
import org.enveloping.ecobin.common.exception.BusinessException;
import org.enveloping.ecobin.framework.security.JwtTokenProvider;
import org.enveloping.ecobin.framework.wechat.WechatMiniappClient;
import org.enveloping.ecobin.framework.wechat.WechatSessionResponse;
import org.enveloping.ecobin.system.dto.LoginRequest;
import org.enveloping.ecobin.system.dto.LoginResponse;
import org.enveloping.ecobin.system.dto.WxLoginRequest;
import org.enveloping.ecobin.system.entity.Admin;
import org.enveloping.ecobin.system.entity.Tenant;
import org.enveloping.ecobin.system.entity.User;
import org.enveloping.ecobin.system.mapper.UserMapper;
import org.enveloping.ecobin.system.service.AdminService;
import org.enveloping.ecobin.system.service.AuthService;
import org.enveloping.ecobin.system.service.TenantService;
import org.enveloping.ecobin.system.service.UserService;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;

/**
 * 认证服务实现。
 */
@Service
@RequiredArgsConstructor
public class AuthServiceImpl implements AuthService {

    private final AdminService adminService;
    private final TenantService tenantService;
    private final UserService userService;
    private final UserMapper userMapper;
    private final PasswordEncoder passwordEncoder;
    private final JwtTokenProvider jwtTokenProvider;
    private final WechatMiniappClient wechatMiniappClient;

    @Override
    public LoginResponse login(LoginRequest request) {
        String userType = request.getUserType() == null ? "admin" : request.getUserType();
        return switch (userType) {
            case "tenant" -> tenantLogin(request);
            case "admin" -> adminLogin(request);
            default -> throw new BusinessException(400, "不支持的登录类型: " + userType);
        };
    }

    private LoginResponse adminLogin(LoginRequest request) {
        Admin admin = adminService.getByUsername(request.getUsername());
        if (admin == null) {
            throw new BusinessException(401, "用户名或密码错误");
        }
        if (admin.getStatus() != null && admin.getStatus() == 0) {
            throw new BusinessException(403, "账号已被禁用");
        }
        if (!passwordEncoder.matches(request.getPassword(), admin.getPassword())) {
            throw new BusinessException(401, "用户名或密码错误");
        }
        // 平台域：tenant 上下文固定平台池
        String token = jwtTokenProvider.generateToken(
                admin.getId(), admin.getUsername(), Constants.PLATFORM_POOL_TENANT_ID, admin.getRole());
        return LoginResponse.builder()
                .token(token)
                .userId(admin.getId())
                .tenantId(Constants.PLATFORM_POOL_TENANT_ID)
                .username(admin.getUsername())
                .realName(admin.getRealName())
                .role(admin.getRole())
                .build();
    }

    private LoginResponse tenantLogin(LoginRequest request) {
        Tenant tenant = tenantService.getByUsername(request.getUsername());
        if (tenant == null) {
            throw new BusinessException(401, "用户名或密码错误");
        }
        if (tenant.getStatus() != null && tenant.getStatus() == 0) {
            throw new BusinessException(403, "账号已被禁用");
        }
        if (tenant.getPassword() == null
                || !passwordEncoder.matches(request.getPassword(), tenant.getPassword())) {
            throw new BusinessException(401, "用户名或密码错误");
        }
        String token = jwtTokenProvider.generateToken(
                tenant.getId(), tenant.getUsername(), tenant.getId(), UserRole.TENANT.getCode());
        return LoginResponse.builder()
                .token(token)
                .userId(tenant.getId())
                .tenantId(tenant.getId())
                .username(tenant.getUsername())
                .realName(tenant.getName())
                .role(UserRole.TENANT.getCode())
                .build();
    }

    @Override
    public LoginResponse wxLogin(WxLoginRequest request) {
        // 1. 按 appid 定位租户，取出（解密后的）secret
        Tenant tenant = tenantService.getByMiniappAppid(request.getAppid());
        if (tenant == null) {
            throw new BusinessException(400, "未找到该小程序对应的租户: " + request.getAppid());
        }
        if (tenant.getStatus() != null && tenant.getStatus() == 0) {
            throw new BusinessException(403, "租户已被禁用");
        }
        String secret = tenantService.decryptMiniappSecret(tenant);

        // 2. 调微信 code2session 换取 openid
        WechatSessionResponse session =
                wechatMiniappClient.code2session(tenant.getMiniappAppid(), secret, request.getCode());
        if (session.getOpenid() == null || session.getOpenid().isEmpty()) {
            throw new BusinessException(500, "获取微信 openid 失败");
        }

        // 3. 查租户下该 openid 的用户，无则自动注册（role=普通用户）
        User user = userMapper.selectByTenantIdAndOpenid(tenant.getId(), session.getOpenid());
        if (user == null) {
            user = new User();
            user.setTenantId(tenant.getId());
            user.setOpenid(session.getOpenid());
            user.setUnionid(session.getUnionid());
            user.setNickname(Constants.WECHAT_NICKNAME_PREFIX + System.currentTimeMillis() % 100000);
            user.setRole(Constants.WECHAT_DEFAULT_ROLE);
            user.setStatus(1);
            userService.save(user);
        }

        // 4. 校验状态
        if (user.getStatus() != null && user.getStatus() == 0) {
            throw new BusinessException(403, "账号已被禁用");
        }

        // 5. 生成 JWT（subject = openid）
        String token = jwtTokenProvider.generateToken(
                user.getId(), user.getOpenid(), tenant.getId(), user.getRole());
        return LoginResponse.builder()
                .token(token)
                .userId(user.getId())
                .tenantId(tenant.getId())
                .username(user.getUsername())
                .realName(user.getRealName())
                .role(user.getRole())
                .nickname(user.getNickname())
                .avatar(user.getAvatar())
                .build();
    }
}
