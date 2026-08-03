package org.enveloping.ecobin.identity.application.security;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.WalletAdjustmentAuthorizationPort;
import org.enveloping.ecobin.identity.api.query.WalletAdjustmentAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedWalletAdjustment;
import org.enveloping.ecobin.identity.application.persistence.WalletAdjustmentRefFactory;
import org.enveloping.ecobin.identity.application.security.DeliveryScopeAuthorizationRepository.Membership;
import org.enveloping.ecobin.identity.application.security.DeliveryScopeAuthorizationRepository.OrganizationUser;
import org.enveloping.ecobin.identity.application.security.DeliveryScopeAuthorizationRepository.PlatformActor;
import org.enveloping.ecobin.identity.application.security.DeliveryScopeAuthorizationRepository.Scope;
import org.enveloping.ecobin.identity.application.security.DeliveryScopeAuthorizationRepository.StaffActor;
import org.enveloping.ecobin.identity.application.web.TargetWebActor;
import org.enveloping.ecobin.identity.application.web.TargetWebActorContext;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.Set;

@Service
public class WalletAdjustmentAuthorizationService
        implements WalletAdjustmentAuthorizationPort {

    private static final String WALLET_ADJUST = "wallet.adjust";

    private final DeliveryScopeAuthorizationRepository repository;
    private final WalletAdjustmentRefFactory referenceFactory;

    public WalletAdjustmentAuthorizationService(
            DeliveryScopeAuthorizationRepository repository,
            WalletAdjustmentRefFactory referenceFactory) {
        this.repository = repository;
        this.referenceFactory = referenceFactory;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public AuthorizedWalletAdjustment authorize(
            WalletAdjustmentAuthorizationQuery query) {
        TargetWebActor actor = TargetWebActorContext.required();
        if (query.platformPath()) {
            return platform(actor, query);
        }
        return staff(actor, query);
    }

    private AuthorizedWalletAdjustment platform(
            TargetWebActor actor,
            WalletAdjustmentAuthorizationQuery query) {
        if (!actor.platform()) throw forbidden();
        PlatformActor current = repository.findPlatformActor(
                        actor.principalId(), actor.principalUid(), true)
                .filter(row -> row.enabled()
                        && row.authVersion() == actor.authVersion())
                .orElseThrow(
                        WalletAdjustmentAuthorizationService::sessionInvalid);
        Scope scope = repository.findPlatformScope(
                        query.tenantCode(), query.organizationCode(), true)
                .filter(Scope::organizationEnabled)
                .orElseThrow(
                        WalletAdjustmentAuthorizationService::notFound);
        OrganizationUser user = requiredUser(scope, query);
        return authorized(
                actor, current.displayName(), scope, user,
                current.id(), null);
    }

    private AuthorizedWalletAdjustment staff(
            TargetWebActor actor,
            WalletAdjustmentAuthorizationQuery query) {
        if (actor.platform()) throw forbidden();
        StaffActor current = repository.findStaffActor(
                        actor.principalId(), actor.principalUid(), true)
                .filter(row -> row.enabled()
                        && row.tenantEnabled()
                        && row.authVersion() == actor.authVersion())
                .orElseThrow(
                        WalletAdjustmentAuthorizationService::sessionInvalid);
        Scope scope = repository.findStaffScope(
                        current.tenantId(), query.organizationCode(), true)
                .filter(Scope::organizationEnabled)
                .orElseThrow(
                        WalletAdjustmentAuthorizationService::notFound);
        if (!canAdjust(current, scope)) throw forbidden();
        OrganizationUser user = requiredUser(scope, query);
        return authorized(
                actor, current.displayName(), scope, user,
                null, current.id());
    }

    private boolean canAdjust(StaffActor actor, Scope scope) {
        if ("TENANT_PRINCIPAL".equals(actor.accountKind())) return true;
        Set<String> tenant = repository.findTenantDeliveryCapabilities(
                actor.tenantId(), actor.id());
        if (tenant.contains(WALLET_ADJUST)) return true;
        Membership membership = repository.findMembership(
                        actor.tenantId(), scope.organizationId(), actor.id())
                .filter(Membership::enabled)
                .orElse(null);
        if (membership == null) return false;
        return membership.manager()
                || repository.findOrganizationDeliveryCapabilities(
                                actor.tenantId(), scope.organizationId(),
                                actor.id())
                        .contains(WALLET_ADJUST);
    }

    private OrganizationUser requiredUser(
            Scope scope,
            WalletAdjustmentAuthorizationQuery query) {
        return repository.findOrganizationUser(
                        scope.tenantId(), scope.organizationId(),
                        query.organizationUserUid(), true)
                .orElseThrow(
                        WalletAdjustmentAuthorizationService::notFound);
    }

    private AuthorizedWalletAdjustment authorized(
            TargetWebActor actor,
            String displayName,
            Scope scope,
            OrganizationUser user,
            Long platformAdminId,
            Long staffAccountId) {
        return new AuthorizedWalletAdjustment(
                actor.platform(), actor.principalUid(), actor.sessionUid(),
                displayName, scope.tenantCode(), scope.organizationCode(),
                user.uid(),
                referenceFactory.issueScope(
                        scope.tenantId(), scope.organizationId()),
                referenceFactory.issueTarget(
                        scope.tenantId(), scope.organizationId(),
                        user.id(), user.uid(),
                        platformAdminId, staffAccountId));
    }

    private static TargetApiException sessionInvalid() {
        return new TargetApiException(
                401, "AUTH.SESSION_INVALID", "当前 Web 会话已失效");
    }

    private static TargetApiException forbidden() {
        return new TargetApiException(
                403, "AUTH.CAPABILITY_REQUIRED",
                "当前账号缺少目标机构的余额调整权限");
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404, "RESOURCE.NOT_FOUND",
                "目标机构或机构用户不存在于当前可操作范围");
    }
}
