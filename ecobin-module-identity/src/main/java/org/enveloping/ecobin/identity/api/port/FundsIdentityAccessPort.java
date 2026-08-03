package org.enveloping.ecobin.identity.api.port;

import java.util.UUID;

/**
 * 资金模块所需的当前登录身份投影与授权边界。
 *
 * <p>实现由 identity 模块持有，调用方不能直接读取身份模块内部的
 * ThreadLocal 登录对象。</p>
 */
public interface FundsIdentityAccessPort {

    AuthorizedWebIdentity authorizeWeb(
            boolean platformPath,
            String organizationCode,
            String capability,
            boolean writeRequiresStaff);

    CurrentMiniappIdentity currentMiniapp(boolean requirePhone);

    AuthorizedPlatformIdentity authorizePlatform();

    record AuthorizedWebIdentity(
            boolean platform,
            String tenantCode,
            long principalId,
            UUID principalUid,
            UUID sessionUid,
            String displayName) {
    }

    record CurrentMiniappIdentity(
            long tenantId,
            String tenantCode,
            long organizationId,
            String organizationCode,
            long organizationMiniappId,
            String appid,
            long organizationUserId,
            UUID organizationUserUid,
            UUID sessionUid,
            String displayName) {
    }

    record AuthorizedPlatformIdentity(
            long principalId,
            UUID principalUid,
            UUID sessionUid,
            String displayName) {
    }
}
