package org.enveloping.ecobin.identity.application.legacy;

import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.common.exception.BusinessException;
import org.enveloping.ecobin.framework.security.JwtTokenProvider;
import org.enveloping.ecobin.identity.api.legacy.LegacyMiniappRegistrationPort;
import org.enveloping.ecobin.identity.api.legacy.LegacyMiniappRegistrationResult;
import org.enveloping.ecobin.identity.web.legacy.dto.LoginResponse;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

/**
 * 微信外调成功后的本地原子步骤：首次身份、资金初始化参与者与本地会话响应同成同败。
 */
@Service
@RequiredArgsConstructor
public class MiniappLoginTransactionService {

    private final LegacyMiniappRegistrationPort miniappRegistrationPort;
    private final JwtTokenProvider jwtTokenProvider;

    @Transactional(propagation = Propagation.REQUIRED)
    public LoginResponse complete(long tenantId, String openid, String unionid) {
        LegacyMiniappRegistrationResult registration =
                miniappRegistrationPort.registerOrFind(tenantId, openid, unionid);
        if (registration.status() == 0) {
            throw new BusinessException(403, "账号已被禁用");
        }

        String token = jwtTokenProvider.generateToken(
                registration.userId().value(),
                registration.openid(),
                tenantId,
                registration.role());
        return LoginResponse.builder()
                .token(token)
                .userId(registration.userId().value())
                .tenantId(tenantId)
                .username(registration.username())
                .realName(registration.realName())
                .role(registration.role())
                .nickname(registration.nickname())
                .avatar(registration.avatar())
                .build();
    }
}
