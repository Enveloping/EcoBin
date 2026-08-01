package org.enveloping.ecobin.identity.application.security;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.DeliveryScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.query.DeliveryScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedDeliveryScope;
import org.enveloping.ecobin.identity.application.security.DeliveryScopeAuthorizationRepository.Membership;
import org.enveloping.ecobin.identity.application.security.DeliveryScopeAuthorizationRepository.PlatformActor;
import org.enveloping.ecobin.identity.application.security.DeliveryScopeAuthorizationRepository.Scope;
import org.enveloping.ecobin.identity.application.security.DeliveryScopeAuthorizationRepository.StaffActor;
import org.enveloping.ecobin.identity.application.web.TargetWebActor;
import org.enveloping.ecobin.identity.application.web.TargetWebActorContext;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.Set;

/**
 * Revalidates delivery Web access in the caller-owned transaction.
 *
 * <p>Read-only transactions do not lock identity rows. Review and correction
 * transactions lock the actor and target scope permanent rows before the
 * recycling aggregate takes its own locks.</p>
 */
@Service
public class DeliveryScopeAuthorizationService
        implements DeliveryScopeAuthorizationPort {

    static final String DELIVERY_READ = "delivery.read";
    static final String REVIEW_EXECUTE = "review.execute";
    static final String DELIVERY_CORRECT = "delivery.correct";
    static final String DELIVERY_CONFIGURATION_MANAGE =
            "delivery.configuration.manage";
    static final String CLEAN_READ = "clean.read";
    static final String CLEAN_EDIT = "clean.edit";

    private final DeliveryScopeAuthorizationRepository repository;
    private final DeliveryScopePersistenceRefFactory referenceFactory;

    public DeliveryScopeAuthorizationService(
            DeliveryScopeAuthorizationRepository repository,
            DeliveryScopePersistenceRefFactory referenceFactory) {
        this.repository = repository;
        this.referenceFactory = referenceFactory;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public AuthorizedDeliveryScope authorize(
            DeliveryScopeAuthorizationQuery query) {
        TargetWebActor actor = TargetWebActorContext.required();
        boolean forUpdate = !TransactionSynchronizationManager
                .isCurrentTransactionReadOnly();
        if (query.platformPath()) {
            return authorizePlatform(actor, query, forUpdate);
        }
        return authorizeStaff(actor, query, forUpdate);
    }

    private AuthorizedDeliveryScope authorizePlatform(
            TargetWebActor actor,
            DeliveryScopeAuthorizationQuery query,
            boolean forUpdate) {
        if (!actor.platform()) {
            throw forbidden();
        }
        PlatformActor current = repository.findPlatformActor(
                        actor.principalId(),
                        actor.principalUid(),
                        forUpdate)
                .filter(row -> row.enabled()
                        && row.authVersion() == actor.authVersion())
                .orElseThrow(DeliveryScopeAuthorizationService::sessionInvalid);
        Scope scope = repository.findPlatformScope(
                        query.tenantCode(),
                        query.organizationCode(),
                        forUpdate)
                .orElseThrow(DeliveryScopeAuthorizationService::notFound);
        return authorized(
                actor,
                current.displayName(),
                scope,
                true,
                true,
                true,
                true,
                true,
                true,
                current.id(),
                null);
    }

    private AuthorizedDeliveryScope authorizeStaff(
            TargetWebActor actor,
            DeliveryScopeAuthorizationQuery query,
            boolean forUpdate) {
        if (actor.platform()) {
            throw forbidden();
        }
        StaffActor current = repository.findStaffActor(
                        actor.principalId(),
                        actor.principalUid(),
                        forUpdate)
                .filter(row -> row.enabled()
                        && row.tenantEnabled()
                        && row.authVersion() == actor.authVersion())
                .orElseThrow(DeliveryScopeAuthorizationService::sessionInvalid);
        Scope scope = repository.findStaffScope(
                        current.tenantId(),
                        query.organizationCode(),
                        forUpdate)
                .orElseThrow(DeliveryScopeAuthorizationService::notFound);
        if (!scope.organizationEnabled()) {
            throw notFound();
        }

        Access access = access(current, scope);
        if (!access.visible()) {
            throw notFound();
        }
        if (!access.anyCapability()) {
            throw forbidden();
        }
        return authorized(
                actor,
                current.displayName(),
                scope,
                access.deliveryRead(),
                access.reviewExecute(),
                access.deliveryCorrect(),
                access.deliveryConfigurationManage(),
                access.cleanRead(),
                access.cleanEdit(),
                null,
                current.id());
    }

    private Access access(StaffActor actor, Scope scope) {
        if ("TENANT_PRINCIPAL".equals(actor.accountKind())) {
            return Access.all();
        }
        Set<String> tenantCapabilities =
                repository.findTenantDeliveryCapabilities(
                        actor.tenantId(),
                        actor.id());
        Membership membership = repository.findMembership(
                        actor.tenantId(),
                        scope.organizationId(),
                        actor.id())
                .filter(Membership::enabled)
                .orElse(null);
        Set<String> organizationCapabilities = membership == null
                ? Set.of()
                : repository.findOrganizationDeliveryCapabilities(
                        actor.tenantId(),
                        scope.organizationId(),
                        actor.id());
        boolean manager = membership != null && membership.manager();
        return new Access(
                !tenantCapabilities.isEmpty() || membership != null,
                manager
                        || tenantCapabilities.contains(DELIVERY_READ)
                        || organizationCapabilities.contains(DELIVERY_READ),
                manager
                        || tenantCapabilities.contains(REVIEW_EXECUTE)
                        || organizationCapabilities.contains(REVIEW_EXECUTE),
                manager
                        || tenantCapabilities.contains(DELIVERY_CORRECT)
                        || organizationCapabilities.contains(DELIVERY_CORRECT),
                manager
                        || tenantCapabilities.contains(
                                DELIVERY_CONFIGURATION_MANAGE)
                        || organizationCapabilities.contains(
                                DELIVERY_CONFIGURATION_MANAGE),
                manager
                        || tenantCapabilities.contains(CLEAN_READ)
                        || organizationCapabilities.contains(CLEAN_READ),
                manager
                        || tenantCapabilities.contains(CLEAN_EDIT)
                        || organizationCapabilities.contains(CLEAN_EDIT));
    }

    private AuthorizedDeliveryScope authorized(
            TargetWebActor actor,
            String displayName,
            Scope scope,
            boolean deliveryRead,
            boolean reviewExecute,
            boolean deliveryCorrect,
            boolean deliveryConfigurationManage,
            boolean cleanRead,
            boolean cleanEdit,
            Long platformAdminId,
            Long staffAccountId) {
        return new AuthorizedDeliveryScope(
                actor.platform(),
                actor.principalUid(),
                actor.sessionUid(),
                displayName,
                scope.tenantCode(),
                scope.organizationCode(),
                deliveryRead,
                reviewExecute,
                deliveryCorrect,
                deliveryConfigurationManage,
                cleanRead,
                cleanEdit,
                referenceFactory.issue(
                        scope.tenantId(),
                        scope.organizationId(),
                        platformAdminId,
                        staffAccountId));
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
                "当前账号缺少目标机构所需的业务能力");
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404,
                "RESOURCE.NOT_FOUND",
                "目标租户或机构不存在于当前业务可见范围");
    }

    private record Access(
            boolean visible,
            boolean deliveryRead,
            boolean reviewExecute,
            boolean deliveryCorrect,
            boolean deliveryConfigurationManage,
            boolean cleanRead,
            boolean cleanEdit) {

        private static Access all() {
            return new Access(
                    true, true, true, true, true, true, true);
        }

        private boolean anyCapability() {
            return deliveryRead || reviewExecute || deliveryCorrect
                    || deliveryConfigurationManage
                    || cleanRead || cleanEdit;
        }
    }
}
