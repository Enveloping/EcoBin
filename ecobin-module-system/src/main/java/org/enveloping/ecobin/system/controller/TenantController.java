package org.enveloping.ecobin.system.controller;

import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.common.exception.BusinessException;
import org.enveloping.ecobin.common.result.Result;
import org.enveloping.ecobin.framework.security.SecurityUtils;
import org.enveloping.ecobin.system.entity.Tenant;
import org.enveloping.ecobin.system.service.TenantService;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * 租户管理接口
 */
@RestController
@RequestMapping("/api/system/tenant")
@RequiredArgsConstructor
public class TenantController {

    private final TenantService tenantService;

    @GetMapping
    public Result<List<Tenant>> list() {
        return Result.ok(tenantService.list());
    }

    /** 租户自查：查看自身资料（仅本人，敏感字段已脱敏） */
    @GetMapping("/me")
    public Result<Tenant> me() {
        Long tenantId = SecurityUtils.getCurrentUserId();
        Tenant tenant = tenantService.getById(tenantId);
        if (tenant == null) {
            throw new BusinessException(404, "租户不存在");
        }
        return Result.ok(tenant);
    }

    @GetMapping("/{id}")
    public Result<Tenant> get(@PathVariable Long id) {
        return Result.ok(tenantService.getById(id));
    }

    @PostMapping
    public Result<Tenant> create(@RequestBody Tenant tenant) {
        tenantService.save(tenant);
        return Result.ok(tenant);
    }

    @PutMapping("/{id}")
    public Result<Tenant> update(@PathVariable Long id, @RequestBody Tenant tenant) {
        tenant.setId(id);
        tenantService.updateById(tenant);
        return Result.ok(tenantService.getById(id));
    }

    @DeleteMapping("/{id}")
    public Result<Void> delete(@PathVariable Long id) {
        tenantService.removeById(id);
        return Result.ok();
    }
}
