package org.enveloping.ecobin.identity.application.legacy;

import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.common.constant.Constants;
import org.enveloping.ecobin.identity.api.command.OrganizationUserRegistrationCommand;
import org.enveloping.ecobin.identity.api.legacy.LegacyMiniappRegistrationPort;
import org.enveloping.ecobin.identity.api.legacy.LegacyMiniappRegistrationResult;
import org.enveloping.ecobin.identity.api.legacy.LegacyOrganizationUserId;
import org.enveloping.ecobin.identity.api.port.OrganizationUserRegistrationParticipant;
import org.enveloping.ecobin.identity.application.persistence.OrganizationUserWalletOwnerRefFactory;
import org.enveloping.ecobin.identity.application.support.LegacyIdentityUidFactory;
import org.enveloping.ecobin.identity.infrastructure.persistence.entity.User;
import org.enveloping.ecobin.identity.infrastructure.persistence.mapper.UserMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;

/**
 * F-02 迁移期首次微信用户注册事务。
 */
@Service
@RequiredArgsConstructor
public class MiniappRegistrationService implements LegacyMiniappRegistrationPort {

    private final UserMapper userMapper;
    private final UserService userService;
    private final OrganizationUserRegistrationParticipant registrationParticipant;
    private final OrganizationUserWalletOwnerRefFactory walletOwnerRefFactory;

    @Transactional(propagation = Propagation.REQUIRED)
    @Override
    public LegacyMiniappRegistrationResult registerOrFind(long tenantId, String openid, String unionid) {
        User existing = userMapper.selectByTenantIdAndOpenid(tenantId, openid);
        if (existing != null) {
            return result(existing, false);
        }

        Instant registeredAt = Instant.now();
        User created = new User();
        created.setTenantId(tenantId);
        created.setOpenid(openid);
        created.setUnionid(unionid);
        created.setNickname(Constants.WECHAT_NICKNAME_PREFIX + System.currentTimeMillis() % 100000);
        created.setRole(Constants.WECHAT_DEFAULT_ROLE);
        created.setStatus(1);
        userService.save(created);

        registrationParticipant.initializeWallet(new OrganizationUserRegistrationCommand(
                LegacyIdentityUidFactory.tenant(tenantId),
                LegacyIdentityUidFactory.organization(tenantId),
                LegacyIdentityUidFactory.organizationUser(tenantId, created.getId()),
                registeredAt,
                walletOwnerRefFactory.issue(
                        tenantId,
                        tenantId,
                        created.getId())));

        return result(created, true);
    }

    private static LegacyMiniappRegistrationResult result(User user, boolean newRegistration) {
        return new LegacyMiniappRegistrationResult(
                new LegacyOrganizationUserId(user.getId()),
                user.getTenantId(),
                user.getOpenid(),
                user.getUsername(),
                user.getRealName(),
                user.getRole(),
                user.getStatus(),
                user.getNickname(),
                user.getAvatar(),
                newRegistration);
    }
}
