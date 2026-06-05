package org.enveloping.ecobin.system.controller;

import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.common.exception.BusinessException;
import org.enveloping.ecobin.common.result.Result;
import org.enveloping.ecobin.framework.security.SecurityUtils;
import org.enveloping.ecobin.system.dto.UserProfileVO;
import org.enveloping.ecobin.system.entity.User;
import org.enveloping.ecobin.system.service.UserService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 小程序终端用户 C 端接口：个人信息（仅本人）。
 * <p>
 * 根据当前登录用户 id 返回脱敏后的个人信息；租户拦截器保证只能命中本租户记录。
 */
@RestController
@RequestMapping("/api/app/profile")
@RequiredArgsConstructor
public class AppProfileController {

    private final UserService userService;

    /** 我的个人信息 */
    @GetMapping
    public Result<UserProfileVO> profile() {
        Long userId = SecurityUtils.getCurrentUserId();
        User user = userService.getById(userId);
        if (user == null) {
            throw new BusinessException(404, "用户不存在");
        }
        return Result.ok(UserProfileVO.from(user));
    }
}
