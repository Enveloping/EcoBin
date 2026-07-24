package org.enveloping.ecobin.identity.application.legacy.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.common.exception.BusinessException;
import org.enveloping.ecobin.framework.crypto.AesCryptoUtil;
import org.enveloping.ecobin.framework.security.TokenInvalidationRegistry;
import org.enveloping.ecobin.identity.api.legacy.LegacyTenantDirectoryPort;
import org.enveloping.ecobin.identity.api.legacy.LegacyTenantDraft;
import org.enveloping.ecobin.identity.api.legacy.LegacyTenantSnapshot;
import org.enveloping.ecobin.identity.application.legacy.TenantService;
import org.enveloping.ecobin.identity.infrastructure.persistence.entity.Tenant;
import org.enveloping.ecobin.identity.infrastructure.persistence.mapper.TenantMapper;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;

/**
 * 租户服务实现：保存时 BCrypt 密码、AES 加密小程序 Secret。
 */
@Service
@RequiredArgsConstructor
public class TenantServiceImpl extends ServiceImpl<TenantMapper, Tenant>
        implements TenantService, LegacyTenantDirectoryPort {

    private final TenantMapper tenantMapper;
    private final PasswordEncoder passwordEncoder;
    private final AesCryptoUtil aesCryptoUtil;
    private final TokenInvalidationRegistry tokenInvalidationRegistry;

    @Override
    public Tenant getByUsername(String username) {
        return tenantMapper.selectByUsername(username);
    }

    @Override
    public Tenant getByMiniappAppid(String appid) {
        return tenantMapper.selectByMiniappAppid(appid);
    }

    @Override
    public String decryptMiniappSecret(Tenant tenant) {
        return tenant == null ? null : aesCryptoUtil.decrypt(tenant.getMiniappSecret());
    }

    @Override
    public boolean save(Tenant tenant) {
        if (tenant.getUsername() != null && tenantMapper.selectByUsername(tenant.getUsername()) != null) {
            throw new BusinessException(400, "租户登录用户名已存在: " + tenant.getUsername());
        }
        if (tenant.getPassword() != null) {
            tenant.setPassword(passwordEncoder.encode(tenant.getPassword()));
        }
        // 入参 miniappSecret 为明文，加密后存储
        if (tenant.getMiniappSecret() != null) {
            tenant.setMiniappSecret(aesCryptoUtil.encrypt(tenant.getMiniappSecret()));
        }
        if (tenant.getStatus() == null) {
            tenant.setStatus(1);
        }
        return super.save(tenant);
    }

    @Override
    public boolean updateById(Tenant tenant) {
        if (tenant.getPassword() != null && !tenant.getPassword().isEmpty()) {
            tenant.setPassword(passwordEncoder.encode(tenant.getPassword()));
        } else {
            tenant.setPassword(null);
        }
        if (tenant.getMiniappSecret() != null && !tenant.getMiniappSecret().isEmpty()) {
            tenant.setMiniappSecret(aesCryptoUtil.encrypt(tenant.getMiniappSecret()));
        } else {
            tenant.setMiniappSecret(null);
        }
        boolean ok = super.updateById(tenant);
        if (ok && tenant.getId() != null && tenant.getStatus() != null) {
            // 状态变更 → 强制该租户重新登录
            tokenInvalidationRegistry.invalidateTenant(tenant.getId());
            // 禁用租户 → 连带其名下全部用户强制下线
            if (tenant.getStatus() == 0) {
                tokenInvalidationRegistry.invalidateTenantUsers(tenant.getId());
            }
        }
        return ok;
    }

    @Override
    public LegacyTenantSnapshot create(LegacyTenantDraft draft) {
        Tenant tenant = new Tenant();
        tenant.setName(draft.name());
        tenant.setCode(draft.code());
        tenant.setUsername(draft.username());
        tenant.setPassword(draft.password());
        tenant.setMiniappAppid(draft.miniappAppid());
        tenant.setMiniappSecret(draft.miniappSecret());
        tenant.setMerchantNo(draft.merchantNo());
        tenant.setContactName(draft.contactName());
        tenant.setContactPhone(draft.contactPhone());
        tenant.setAddress(draft.address());
        tenant.setStatus(draft.status());
        save(tenant);
        return new LegacyTenantSnapshot(
                tenant.getId(),
                tenant.getName(),
                tenant.getCode(),
                tenant.getUsername(),
                tenant.getMiniappAppid(),
                tenant.getMerchantNo(),
                tenant.getContactName(),
                tenant.getContactPhone(),
                tenant.getAddress(),
                tenant.getStatus());
    }
}
