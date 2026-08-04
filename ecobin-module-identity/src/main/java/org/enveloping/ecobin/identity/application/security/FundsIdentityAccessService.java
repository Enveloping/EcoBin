package org.enveloping.ecobin.identity.application.security;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.FundsIdentityAccessPort;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappActor;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappActorContext;
import org.enveloping.ecobin.identity.application.web.TargetWebActor;
import org.enveloping.ecobin.identity.application.web.TargetWebActorContext;
import org.springframework.stereotype.Service;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

@Service
public class FundsIdentityAccessService implements FundsIdentityAccessPort {

    private final JdbcTemplate jdbc;

    public FundsIdentityAccessService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

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

    @Override
    public AuthorizedPlatformIdentity authorizePlatform() {
        TargetWebActor actor = TargetWebActorContext.required();
        if (!actor.platform()) throw forbidden();
        return new AuthorizedPlatformIdentity(
                actor.principalId(), actor.principalUid(),
                actor.sessionUid(), actor.displayName());
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public boolean lockWithdrawalTransferIdentity(
            WithdrawalTransferIdentity identity) {
        if (identity == null
                || identity.tenantId() <= 0
                || identity.organizationId() <= 0
                || identity.organizationMiniappId() <= 0
                || identity.organizationUserId() <= 0
                || identity.appid() == null
                || identity.appid().isBlank()
                || identity.openid() == null
                || identity.openid().isBlank()) {
            throw new IllegalArgumentException(
                    "withdrawal transfer identity is incomplete");
        }
        if (!lockExists("""
                SELECT id FROM iam_tenant
                WHERE id = ? AND status = 'ENABLED'
                FOR UPDATE
                """, identity.tenantId())) {
            return false;
        }
        if (!lockExists("""
                SELECT id FROM iam_organization
                WHERE tenant_id = ? AND id = ? AND status = 'ENABLED'
                FOR UPDATE
                """, identity.tenantId(), identity.organizationId())) {
            return false;
        }
        if (!lockExists("""
                SELECT id FROM iam_organization_miniapp
                WHERE tenant_id = ? AND organization_id = ? AND id = ?
                  AND appid = ? AND login_enabled = 1
                FOR UPDATE
                """, identity.tenantId(), identity.organizationId(),
                identity.organizationMiniappId(), identity.appid())) {
            return false;
        }
        return lockExists("""
                SELECT id FROM iam_organization_user
                WHERE tenant_id = ? AND organization_id = ?
                  AND organization_miniapp_id = ? AND id = ?
                  AND openid = ? AND status = 'ACTIVE'
                FOR UPDATE
                """, identity.tenantId(), identity.organizationId(),
                identity.organizationMiniappId(),
                identity.organizationUserId(), identity.openid());
    }

    private boolean lockExists(String sql, Object... arguments) {
        List<Long> rows = jdbc.query(
                sql, (resultSet, ignored) -> resultSet.getLong("id"),
                arguments);
        return rows.size() == 1;
    }

    private static TargetApiException forbidden() {
        return new TargetApiException(
                403,
                "AUTH.CAPABILITY_REQUIRED",
                "当前账号缺少该机构资金操作能力");
    }
}
