package org.enveloping.ecobin.funds.application.access;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.FundsIdentityAccessPort;
import org.enveloping.ecobin.identity.api.port.FundsIdentityAccessPort.AuthorizedWebIdentity;
import org.enveloping.ecobin.identity.api.port.FundsIdentityAccessPort.CurrentMiniappIdentity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.UUID;

@Service
public class FundsAccessService {

    private final JdbcTemplate jdbc;
    private final FundsIdentityAccessPort identity;

    public FundsAccessService(
            JdbcTemplate jdbc,
            FundsIdentityAccessPort identity) {
        this.jdbc = jdbc;
        this.identity = identity;
    }

    public WebScope webScope(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String capability,
            boolean writeRequiresStaff) {
        AuthorizedWebIdentity actor = identity.authorizeWeb(
                platformPath, organizationCode, capability,
                writeRequiresStaff);
        String effectiveTenant = platformPath
                ? tenantCode
                : actor.tenantCode();
        List<WebScope> scopes = jdbc.query("""
                SELECT t.id AS tenant_id, t.tenant_code,
                       o.id AS organization_id, o.organization_code
                FROM iam_tenant t
                JOIN iam_organization o ON o.tenant_id = t.id
                WHERE t.tenant_code = ?
                  AND o.organization_code = ?
                  AND t.status = 'ENABLED'
                  AND o.status = 'ENABLED'
                """, (rs, ignored) -> new WebScope(
                        rs.getLong("tenant_id"),
                        rs.getString("tenant_code"),
                        rs.getLong("organization_id"),
                        rs.getString("organization_code"),
                        actor.platform() ? actor.principalId() : null,
                        actor.platform() ? null : actor.principalId(),
                        actor.principalUid(), actor.sessionUid(),
                        actor.displayName()),
                effectiveTenant, organizationCode);
        if (scopes.isEmpty()) {
            throw notFound();
        }
        return scopes.getFirst();
    }

    public MiniappScope miniappScope(boolean requirePhone) {
        CurrentMiniappIdentity actor = identity.currentMiniapp(requirePhone);
        return new MiniappScope(
                actor.tenantId(), actor.tenantCode(),
                actor.organizationId(), actor.organizationCode(),
                actor.organizationMiniappId(), actor.appid(),
                actor.organizationUserId(), actor.organizationUserUid(),
                actor.sessionUid(), actor.displayName());
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404,
                "RESOURCE.NOT_FOUND",
                "目标机构不存在于当前资金操作范围");
    }

    public record WebScope(
            long tenantId,
            String tenantCode,
            long organizationId,
            String organizationCode,
            Long platformAdminId,
            Long staffAccountId,
            UUID actorUid,
            UUID sessionUid,
            String actorDisplayName) {

        public boolean platformActor() {
            return platformAdminId != null;
        }
    }

    public record MiniappScope(
            long tenantId,
            String tenantCode,
            long organizationId,
            String organizationCode,
            long organizationMiniappId,
            String appid,
            long organizationUserId,
            UUID organizationUserUid,
            UUID sessionUid,
            String actorDisplayName) {
    }
}
