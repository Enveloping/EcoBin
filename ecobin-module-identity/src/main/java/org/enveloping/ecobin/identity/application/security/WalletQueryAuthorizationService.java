package org.enveloping.ecobin.identity.application.security;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.WalletQueryAuthorizationPort;
import org.enveloping.ecobin.identity.api.query.WalletScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedWalletScope;
import org.enveloping.ecobin.identity.application.persistence.DeliveryIdentityQueryRefFactory;
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
public class WalletQueryAuthorizationService
        implements WalletQueryAuthorizationPort {

    private static final String WALLET_READ = "wallet.read";

    private final DeliveryScopeAuthorizationRepository repository;
    private final DeliveryIdentityQueryRefFactory referenceFactory;

    public WalletQueryAuthorizationService(
            DeliveryScopeAuthorizationRepository repository,
            DeliveryIdentityQueryRefFactory referenceFactory) {
        this.repository = repository;
        this.referenceFactory = referenceFactory;
    }

    @Override
    @Transactional(
            propagation = Propagation.MANDATORY,
            readOnly = true)
    public AuthorizedWalletScope authorize(
            WalletScopeAuthorizationQuery query) {
        TargetWebActor actor = TargetWebActorContext.required();
        if (query.platformPath()) {
            if (!actor.platform()) {
                throw forbidden();
            }
            PlatformActor current = repository.findPlatformActor(
                            actor.principalId(),
                            actor.principalUid(),
                            false)
                    .filter(row -> row.enabled()
                            && row.authVersion()
                            == actor.authVersion())
                    .orElseThrow(
                            WalletQueryAuthorizationService
                                    ::sessionInvalid);
            Scope scope = repository.findPlatformScope(
                            query.tenantCode(),
                            query.organizationCode(),
                            false)
                    .orElseThrow(
                            WalletQueryAuthorizationService::notFound);
            return authorized(scope, query, true);
        }

        if (actor.platform()) {
            throw forbidden();
        }
        StaffActor current = repository.findStaffActor(
                        actor.principalId(),
                        actor.principalUid(),
                        false)
                .filter(row -> row.enabled()
                        && row.tenantEnabled()
                        && row.authVersion() == actor.authVersion())
                .orElseThrow(
                        WalletQueryAuthorizationService::sessionInvalid);
        Scope scope = repository.findStaffScope(
                        current.tenantId(),
                        query.organizationCode(),
                        false)
                .filter(Scope::organizationEnabled)
                .orElseThrow(
                        WalletQueryAuthorizationService::notFound);
        if (!canReadWallet(current, scope)) {
            throw forbidden();
        }
        return authorized(scope, query, false);
    }

    private boolean canReadWallet(StaffActor actor, Scope scope) {
        if ("TENANT_PRINCIPAL".equals(actor.accountKind())) {
            return true;
        }
        Set<String> tenantCapabilities =
                repository.findTenantDeliveryCapabilities(
                        actor.tenantId(),
                        actor.id());
        if (tenantCapabilities.contains(WALLET_READ)) {
            return true;
        }
        Membership membership = repository.findMembership(
                        actor.tenantId(),
                        scope.organizationId(),
                        actor.id())
                .filter(Membership::enabled)
                .orElse(null);
        if (membership == null) {
            return false;
        }
        return membership.manager()
                || repository.findOrganizationDeliveryCapabilities(
                                actor.tenantId(),
                                scope.organizationId(),
                                actor.id())
                        .contains(WALLET_READ);
    }

    private AuthorizedWalletScope authorized(
            Scope scope,
            WalletScopeAuthorizationQuery query,
            boolean platformActor) {
        OrganizationUser user = query.organizationUserUid() == null
                ? null
                : repository.findOrganizationUser(
                                scope.tenantId(),
                                scope.organizationId(),
                                query.organizationUserUid())
                        .orElseThrow(
                                WalletQueryAuthorizationService::notFound);
        Long userId = user == null ? null : user.id();
        java.util.UUID userUid = user == null ? null : user.uid();
        return new AuthorizedWalletScope(
                platformActor,
                scope.tenantCode(),
                scope.organizationCode(),
                userUid,
                referenceFactory.issueWalletScope(
                        scope.tenantId(),
                        scope.organizationId(),
                        userId,
                        userUid),
                user == null
                        ? null
                        : referenceFactory.issueWalletQueryOwner(
                        scope.tenantId(),
                        scope.organizationId(),
                        user.id(),
                        user.uid()),
                user == null
                        ? null
                        : referenceFactory.issueDeliveryQueryUser(
                        scope.tenantId(),
                        scope.organizationId(),
                        user.id(),
                        user.uid()));
    }

    private static TargetApiException sessionInvalid() {
        return new TargetApiException(
                401,
                "AUTH.SESSION_INVALID",
                "当前 Web 会话已失效");
    }

    private static TargetApiException forbidden() {
        return new TargetApiException(
                403,
                "AUTH.CAPABILITY_REQUIRED",
                "当前账号缺少钱包查询能力");
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404,
                "RESOURCE.NOT_FOUND",
                "目标机构或机构用户不存在于当前钱包可见范围");
    }
}
