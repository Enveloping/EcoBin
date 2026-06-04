package org.enveloping.ecobin.system.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.common.exception.BusinessException;
import org.enveloping.ecobin.framework.security.TokenInvalidationRegistry;
import org.enveloping.ecobin.system.entity.Admin;
import org.enveloping.ecobin.system.mapper.AdminMapper;
import org.enveloping.ecobin.system.service.AdminService;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;

/**
 * 平台管理员服务实现
 */
@Service
@RequiredArgsConstructor
public class AdminServiceImpl extends ServiceImpl<AdminMapper, Admin> implements AdminService {

    private final AdminMapper adminMapper;
    private final PasswordEncoder passwordEncoder;
    private final TokenInvalidationRegistry tokenInvalidationRegistry;

    @Override
    public Admin getByUsername(String username) {
        return adminMapper.selectByUsername(username);
    }

    @Override
    public boolean save(Admin admin) {
        if (admin.getUsername() != null && adminMapper.selectByUsername(admin.getUsername()) != null) {
            throw new BusinessException(400, "管理员用户名已存在: " + admin.getUsername());
        }
        if (admin.getPassword() != null) {
            admin.setPassword(passwordEncoder.encode(admin.getPassword()));
        }
        if (admin.getStatus() == null) {
            admin.setStatus(1);
        }
        return super.save(admin);
    }

    @Override
    public boolean updateById(Admin admin) {
        // 传了明文密码则加密；否则保持原值（前端不回传则不更新密码）
        if (admin.getPassword() != null && !admin.getPassword().isEmpty()) {
            admin.setPassword(passwordEncoder.encode(admin.getPassword()));
        } else {
            admin.setPassword(null);
        }
        boolean ok = super.updateById(admin);
        // 角色或状态变更（如超管禁用其他管理员）→ 强制该管理员重新登录
        if (ok && admin.getId() != null && (admin.getRole() != null || admin.getStatus() != null)) {
            tokenInvalidationRegistry.invalidateAdmin(admin.getId());
        }
        return ok;
    }
}
