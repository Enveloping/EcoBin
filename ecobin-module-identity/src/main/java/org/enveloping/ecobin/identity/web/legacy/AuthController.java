package org.enveloping.ecobin.identity.web.legacy;

import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.common.result.Result;
import org.enveloping.ecobin.identity.application.legacy.AuthService;
import org.enveloping.ecobin.identity.web.legacy.dto.LoginRequest;
import org.enveloping.ecobin.identity.web.legacy.dto.LoginResponse;
import org.enveloping.ecobin.identity.web.legacy.dto.WxLoginRequest;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 认证接口
 */
@RestController
@RequestMapping("/api/system/auth")
@RequiredArgsConstructor
public class AuthController {

    private final AuthService authService;

    /**
     * 网页端登录（管理员/租户，按 userType 区分）
     */
    @PostMapping("/login")
    public Result<LoginResponse> login(@Valid @RequestBody LoginRequest request) {
        LoginResponse response = authService.login(request);
        return Result.ok(response);
    }

    /**
     * 微信小程序登录
     */
    @PostMapping("/wx-login")
    public Result<LoginResponse> wxLogin(@Valid @RequestBody WxLoginRequest request) {
        LoginResponse response = authService.wxLogin(request);
        return Result.ok(response);
    }
}
