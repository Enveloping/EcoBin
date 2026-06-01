package org.enveloping.ecobin.system.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import org.enveloping.ecobin.system.entity.Tenant;
import org.enveloping.ecobin.system.mapper.TenantMapper;
import org.enveloping.ecobin.system.service.TenantService;
import org.springframework.stereotype.Service;

/**
 * 租户服务实现（MyBatis Plus 自动提供 CRUD）
 */
@Service
public class TenantServiceImpl extends ServiceImpl<TenantMapper, Tenant> implements TenantService {
}
