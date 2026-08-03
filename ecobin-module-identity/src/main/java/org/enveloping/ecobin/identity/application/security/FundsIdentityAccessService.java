package org.enveloping.ecobin.identity.application.security;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.FundsIdentityAccessPort;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappActor;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappActorContext;
import org.enveloping.ecobin.identity.application.web.TargetWebActor;
import org.enveloping.ecobin.identity.application.web.TargetWebActorContext;
import org.springframework.stereotype.Service;

@Service
public class FundsIdentityAccessService implements FundsIdentityAccessPort {

    @Override
    public AuthorizedWebIdentity authorizeWeb(
            boolean platformPath,
            String organizationCode,
            String capability,
            boolean writeRequiresStaff) {
        TargetWebActor actor = TargetWebActorContext.required();
        if (platformPath != actor.platform()) {
            throw forbidden();
        }
        if (platformPath && writeRequiresStaff) {
            throw new TargetApiException(
                    403,
                    "FUNDS.STAFF_ACTOR_REQUIRED",
                    "该资金写操作必须由目标租户工作人员发起");
        }
        if (!platformPath
                && !actor.hasOrganizationCapability(
                organizationCode, capability)) {
            throw forbidden();
        }
        return new AuthorizedWebIdentity(
                actor.platform(), actor.tenantCode(), actor.principalId(),
                actor.principalUid(), actor.sessionUid(), actor.displayName());
    }

    @Override
    public CurrentMiniappIdentity currentMiniapp(boolean requirePhone) {
        TargetMiniappActor actor = TargetMiniappActorContext.required();
        if (requirePhone && !actor.phoneBound()) {
            throw new TargetApiException(
                    422,
                    "WITHDRAWAL.PHONE_BINDING_REQUIRED",
                    "发起提现前需要先绑定手机号");
        }
        return new CurrentMiniappIdentity(
                actor.tenantId(), actor.tenantCode(),
                actor.organizationId(), actor.organizationCode(),
                actor.organizationMiniappId(), actor.appId(),
                actor.organizationUserId(), actor.principalUid(),
                actor.sessionUid(), actor.displayName());
    }

    private static TargetApiException forbidden() {
        return new TargetApiException(
                403,
                "AUTH.CAPABILITY_REQUIRED",
                "当前账号缺少该机构资金操作能力");
    }
}
