package org.enveloping.ecobin.system.controller;

import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.common.result.Result;
import org.enveloping.ecobin.system.dto.UserPageQuery;
import org.enveloping.ecobin.system.dto.UserRoleUpdateRequest;
import org.enveloping.ecobin.system.entity.User;
import org.enveloping.ecobin.system.service.UserService;
import org.springframework.web.bind.annotation.*;

/**
 * 用户管理接口
 */
@RestController
@RequestMapping("/api/system/user")
@RequiredArgsConstructor
public class UserController {

    private final UserService userService;

    @GetMapping
    public Result<PageResult<User>> page(UserPageQuery query) {
        return Result.ok(userService.pageUsers(query));
    }

    @GetMapping("/{id}")
    public Result<User> get(@PathVariable Long id) {
        return Result.ok(userService.getById(id));
    }

    @PostMapping
    public Result<User> create(@RequestBody User user) {
        // save() 已覆盖，包含密码加密和用户名唯一校验
        userService.save(user);
        return Result.ok(user);
    }

    @PutMapping("/{id}")
    public Result<User> update(@PathVariable Long id, @RequestBody User user) {
        user.setId(id);
        // updateById() 已覆盖，包含密码加密
        userService.updateById(user);
        return Result.ok(userService.getById(id));
    }

    @DeleteMapping("/{id}")
    public Result<Void> delete(@PathVariable Long id) {
        userService.removeById(id);
        return Result.ok();
    }

    /** 租户提升/降低用户角色（仅限 1/2/3；改后该用户旧 token 立即失效） */
    @PutMapping("/{id}/role")
    public Result<User> changeRole(@PathVariable Long id, @Valid @RequestBody UserRoleUpdateRequest request) {
        return Result.ok(userService.changeRole(id, request.getRole()));
    }
}
