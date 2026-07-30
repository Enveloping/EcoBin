package org.enveloping.ecobin.identity.application.startdelivery;

import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.framework.context.TrustedPrincipalKind;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.enveloping.ecobin.identity.api.port.MiniappWalletIdentityQueryPort;
import org.enveloping.ecobin.identity.api.result.CurrentMiniappWalletIdentity;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappActor;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappActorContext;
import org.enveloping.ecobin.identity.application.persistence.DeliveryIdentityQueryRefFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronizationManager;

@Service
public class MiniappWalletIdentityQueryService
        implements MiniappWalletIdentityQueryPort {

    private final DeliveryIdentityQueryRefFactory referenceFactory;

    MiniappWalletIdentityQueryService(
            DeliveryIdentityQueryRefFactory referenceFactory) {
        this.referenceFactory = referenceFactory;
    }

    @Override
    @Transactional(
            propagation = Propagation.MANDATORY,
            readOnly = true)
    public CurrentMiniappWalletIdentity current() {
        requireReadOnlyTransaction();
        TargetMiniappActor actor = TargetMiniappActorContext.required();
        if (actor.audience() != TrustedAudience.MINIAPP
                || actor.principalKind()
                != TrustedPrincipalKind.ORGANIZATION_USER) {
            throw new TargetApiException(
                    401,
                    "AUTH.TOKEN_AUDIENCE_MISMATCH",
                    "当前登录入口不是机构用户钱包入口");
        }
        return new CurrentMiniappWalletIdentity(
                new OrganizationUserUid(actor.principalUid()),
                referenceFactory.issueDeliveryQueryUser(
                        actor.tenantId(),
                        actor.organizationId(),
                        actor.organizationUserId(),
                        actor.principalUid()),
                referenceFactory.issueWalletQueryOwner(
                        actor.tenantId(),
                        actor.organizationId(),
                        actor.organizationUserId(),
                        actor.principalUid()));
    }

    private static void requireReadOnlyTransaction() {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()
                || !TransactionSynchronizationManager
                .isCurrentTransactionReadOnly()
                || !TransactionSynchronizationManager
                .isSynchronizationActive()) {
            throw new IllegalStateException(
                    "miniapp wallet identity query requires "
                            + "an existing read-only transaction");
        }
    }
}
