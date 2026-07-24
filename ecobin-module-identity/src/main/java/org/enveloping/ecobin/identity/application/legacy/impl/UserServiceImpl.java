package org.enveloping.ecobin.identity.application.legacy.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.common.constant.Constants;
import org.enveloping.ecobin.common.enums.UserRole;
import org.enveloping.ecobin.common.exception.BusinessException;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.framework.security.TokenInvalidationRegistry;
import org.enveloping.ecobin.identity.api.legacy.LegacyOrganizationUserDirectoryPort;
import org.enveloping.ecobin.identity.api.legacy.LegacyOrganizationUserDraft;
import org.enveloping.ecobin.identity.api.legacy.LegacyOrganizationUserFinancePort;
import org.enveloping.ecobin.identity.api.legacy.LegacyOrganizationUserId;
import org.enveloping.ecobin.identity.api.legacy.LegacyOrganizationUserSnapshot;
import org.enveloping.ecobin.identity.api.legacy.LegacyOrganizationUserStatistics;
import org.enveloping.ecobin.identity.api.legacy.LegacyOrganizationUserUpdate;
import org.enveloping.ecobin.identity.application.legacy.UserService;
import org.enveloping.ecobin.identity.infrastructure.persistence.entity.User;
import org.enveloping.ecobin.identity.infrastructure.persistence.mapper.UserMapper;
import org.enveloping.ecobin.identity.web.legacy.dto.UserPageQuery;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class UserServiceImpl extends ServiceImpl<UserMapper, User>
        implements UserService, LegacyOrganizationUserDirectoryPort, LegacyOrganizationUserFinancePort {

    private final UserMapper userMapper;
    private final PasswordEncoder passwordEncoder;
    private final TokenInvalidationRegistry tokenInvalidationRegistry;

    @Override
    public boolean save(User user) {
        requireTerminalRole(user.getRole());
        // 检查用户名唯一性（仅当提供了用户名时）
        if (user.getUsername() != null) {
            User existing = userMapper.selectByUsername(user.getUsername());
            if (existing != null) {
                throw new BusinessException(400, "用户名已存在: " + user.getUsername());
            }
        }
        // 加密密码（仅当提供了密码时，微信用户无密码）
        if (user.getPassword() != null) {
            user.setPassword(passwordEncoder.encode(user.getPassword()));
        }
        if (user.getTenantId() == null) {
            user.setTenantId(Constants.DEFAULT_TENANT_ID);
        }
        return super.save(user);
    }

    @Override
    public boolean updateById(User user) {
        requireTerminalRole(user.getRole());
        // 如果传了密码则加密
        if (user.getPassword() != null) {
            user.setPassword(passwordEncoder.encode(user.getPassword()));
        }
        boolean ok = super.updateById(user);
        // 角色或状态变更 → 强制该用户重新登录
        if (ok && user.getId() != null && (user.getRole() != null || user.getStatus() != null)) {
            tokenInvalidationRegistry.invalidateUser(user.getId());
        }
        return ok;
    }

    @Override
    public User changeRole(Long id, Integer role) {
        requireTerminalRole(role);
        // 租户拦截器已限定本租户；跨租户/不存在统一按"不存在"处理
        User existing = getById(id);
        if (existing == null) {
            throw new BusinessException(404, "用户不存在");
        }
        User update = new User();
        update.setId(id);
        update.setRole(role);
        updateById(update);   // 复用：角色变更登记 token 失效
        return getById(id);
    }

    @Override
    public PageResult<User> pageUsers(UserPageQuery query) {
        Page<User> page = new Page<>(query.getPage(), query.getPageSize());
        Page<User> result = page(page, new LambdaQueryWrapper<User>().orderByAsc(User::getId));
        return PageResult.of(result.getRecords(), result.getTotal(), query.getPage(), query.getPageSize());
    }

    @Override
    public LegacyOrganizationUserSnapshot create(LegacyOrganizationUserDraft draft) {
        User user = new User();
        user.setTenantId(draft.tenantId());
        user.setUsername(draft.username());
        user.setPassword(draft.password());
        user.setRealName(draft.realName());
        user.setPhone(draft.phone());
        user.setEmail(draft.email());
        user.setOpenid(draft.openid());
        user.setUnionid(draft.unionid());
        user.setNickname(draft.nickname());
        user.setAvatar(draft.avatar());
        user.setRole(draft.role());
        user.setStatus(draft.status());
        user.setBalance(draft.balance());
        user.setPendingBalance(draft.pendingBalance());
        save(user);
        return snapshot(user);
    }

    @Override
    public LegacyOrganizationUserSnapshot find(LegacyOrganizationUserId userId) {
        return snapshotOrNull(getById(userId.value()));
    }

    @Override
    public LegacyOrganizationUserSnapshot update(LegacyOrganizationUserUpdate update) {
        User user = new User();
        user.setId(update.userId().value());
        user.setPassword(update.password());
        user.setRealName(update.realName());
        user.setPhone(update.phone());
        user.setEmail(update.email());
        user.setNickname(update.nickname());
        user.setAvatar(update.avatar());
        user.setRole(update.role());
        user.setStatus(update.status());
        updateById(user);
        return find(update.userId());
    }

    @Override
    public LegacyOrganizationUserSnapshot changeRole(LegacyOrganizationUserId userId, int role) {
        return snapshot(changeRole(userId.value(), role));
    }

    @Override
    public LegacyOrganizationUserSnapshot findAccount(LegacyOrganizationUserId userId) {
        return find(userId);
    }

    @Override
    public void addBalance(LegacyOrganizationUserId userId, java.math.BigDecimal amount) {
        userMapper.addBalance(userId.value(), amount);
    }

    @Override
    public boolean freezeForWithdraw(LegacyOrganizationUserId userId, java.math.BigDecimal amount) {
        return userMapper.freezeForWithdraw(userId.value(), amount) != 0;
    }

    @Override
    public void settlePending(LegacyOrganizationUserId userId, java.math.BigDecimal amount) {
        userMapper.settlePending(userId.value(), amount);
    }

    @Override
    public void refundPending(LegacyOrganizationUserId userId, java.math.BigDecimal amount) {
        userMapper.refundPending(userId.value(), amount);
    }

    @Override
    public LegacyOrganizationUserStatistics statistics() {
        return new LegacyOrganizationUserStatistics(
                userMapper.countMembers(),
                userMapper.countTodayMembers(),
                userMapper.countDisabledMembers(),
                userMapper.sumBalance(),
                userMapper.sumPendingBalance());
    }

    private static LegacyOrganizationUserSnapshot snapshotOrNull(User user) {
        return user == null ? null : snapshot(user);
    }

    private static LegacyOrganizationUserSnapshot snapshot(User user) {
        return new LegacyOrganizationUserSnapshot(
                new LegacyOrganizationUserId(user.getId()),
                user.getTenantId(),
                user.getUsername(),
                user.getRealName(),
                user.getPhone(),
                user.getNickname(),
                user.getAvatar(),
                user.getRole(),
                user.getStatus(),
                user.getBalance(),
                user.getPendingBalance());
    }

    /**
     * 校验角色为合法的终端角色（1-普通用户 / 2-清运员 / 3-设备管理员）。
     * sys_user 仅存终端角色；拒绝写入平台/租户角色（7/8/9），杜绝经增改接口跨域提权。
     * role 为 null 视为不改动角色，放行。
     */
    private void requireTerminalRole(Integer role) {
        if (role == null) {
            return;
        }
        if (role != UserRole.USER.getCode()
                && role != UserRole.CLEANER.getCode()
                && role != UserRole.DEVICE_ADMIN.getCode()) {
            throw new BusinessException(400, "非法的用户角色: " + role);
        }
    }
}
