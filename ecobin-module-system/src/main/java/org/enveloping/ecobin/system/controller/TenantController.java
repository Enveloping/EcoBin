package org.enveloping.ecobin.system.controller;

import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.common.result.Result;
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
