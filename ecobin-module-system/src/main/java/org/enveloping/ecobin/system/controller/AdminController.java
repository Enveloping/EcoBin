package org.enveloping.ecobin.system.controller;

import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.common.result.Result;
import org.enveloping.ecobin.system.entity.Admin;
import org.enveloping.ecobin.system.service.AdminService;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * 平台管理员管理接口（仅超管，URL 级已由 SecurityConfig 限定 SUPER_ADMIN）。
 */
@RestController
@RequestMapping("/api/system/admin")
@RequiredArgsConstructor
public class AdminController {

    private final AdminService adminService;

    @GetMapping
    public Result<List<Admin>> list() {
        return Result.ok(adminService.list());
    }

    @GetMapping("/{id}")
    public Result<Admin> get(@PathVariable Long id) {
        return Result.ok(adminService.getById(id));
    }

    @PostMapping
    public Result<Admin> create(@RequestBody Admin admin) {
        adminService.save(admin);
        return Result.ok(admin);
    }

    @PutMapping("/{id}")
    public Result<Admin> update(@PathVariable Long id, @RequestBody Admin admin) {
        admin.setId(id);
        adminService.updateById(admin);
        return Result.ok(adminService.getById(id));
    }

    @DeleteMapping("/{id}")
    public Result<Void> delete(@PathVariable Long id) {
        adminService.removeById(id);
        return Result.ok();
    }
}
