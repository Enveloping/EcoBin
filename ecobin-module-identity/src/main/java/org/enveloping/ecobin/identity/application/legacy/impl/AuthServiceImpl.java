package org.enveloping.ecobin.identity.application.legacy.impl;

import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.common.constant.Constants;
import org.enveloping.ecobin.common.enums.UserRole;
import org.enveloping.ecobin.common.exception.BusinessException;
import org.enveloping.ecobin.framework.security.JwtTokenProvider;
import org.enveloping.ecobin.identity.api.port.WechatSessionPort;
import org.enveloping.ecobin.identity.api.result.WechatSession;
import org.enveloping.ecobin.identity.application.legacy.AdminService;
import org.enveloping.ecobin.identity.application.legacy.AuthService;
import org.enveloping.ecobin.identity.application.legacy.MiniappLoginTransactionService;
import org.enveloping.ecobin.identity.application.legacy.TenantService;
import org.enveloping.ecobin.identity.infrastructure.persistence.entity.Admin;
import org.enveloping.ecobin.identity.infrastructure.persistence.entity.Tenant;
import org.enveloping.ecobin.identity.web.legacy.dto.LoginRequest;
import org.enveloping.ecobin.identity.web.legacy.dto.LoginResponse;
import org.enveloping.ecobin.identity.web.legacy.dto.WxLoginRequest;
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
    private final MiniappLoginTransactionService miniappLoginTransactionService;
    private final PasswordEncoder passwordEncoder;
    private final JwtTokenProvider jwtTokenProvider;
    private final WechatSessionPort wechatSessionPort;

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
        WechatSession session =
                wechatSessionPort.exchange(tenant.getMiniappAppid(), secret, request.getCode());
        if (session.openid() == null || session.openid().isEmpty()) {
            throw new BusinessException(500, "获取微信 openid 失败");
        }

        // 3. 外调结束后才进入本地事务：首次身份、资金参与和本地 JWT 响应同成同败
        return miniappLoginTransactionService.complete(
                tenant.getId(), session.openid(), session.unionid());
    }
}
