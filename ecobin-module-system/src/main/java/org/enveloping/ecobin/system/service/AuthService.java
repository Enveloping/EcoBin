package org.enveloping.ecobin.system.service;

import org.enveloping.ecobin.system.dto.LoginRequest;
import org.enveloping.ecobin.system.dto.LoginResponse;
import org.enveloping.ecobin.system.dto.WxLoginRequest;

/**
 * 认证服务：网页端（管理员/租户）与小程序（微信）登录。
 */
public interface AuthService {

    /** 网页端登录，按 userType 分派 admin / tenant */
    LoginResponse login(LoginRequest request);

    /** 小程序微信登录（按 appid 定位租户） */
    LoginResponse wxLogin(WxLoginRequest request);
}
