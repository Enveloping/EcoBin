package org.enveloping.ecobin.identity.application.startdelivery;

import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.framework.context.TrustedPrincipalKind;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.enveloping.ecobin.identity.api.id.SessionUid;
import org.enveloping.ecobin.identity.api.port.MiniappDeliveryIdentityQueryPort;
import org.enveloping.ecobin.identity.api.result.CurrentMiniappDeliveryIdentity;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappActor;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappActorContext;
import org.enveloping.ecobin.identity.application.persistence.DeliveryIdentityQueryRefFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronizationManager;

@Service
public class MiniappDeliveryIdentityQueryService
        implements MiniappDeliveryIdentityQueryPort {

    private final DeliveryIdentityQueryRefFactory referenceFactory;

    MiniappDeliveryIdentityQueryService(
            DeliveryIdentityQueryRefFactory referenceFactory) {
        this.referenceFactory = referenceFactory;
    }

    @Override
    @Transactional(
            propagation = Propagation.MANDATORY,
            readOnly = true)
    public CurrentMiniappDeliveryIdentity current() {
        requireReadOnlyTransaction();
        TargetMiniappActor actor = TargetMiniappActorContext.required();
        if (actor.audience() != TrustedAudience.MINIAPP
                || actor.principalKind()
                != TrustedPrincipalKind.ORGANIZATION_USER
                || !"USER".equals(actor.entryMode())) {
            throw new TargetApiException(
                    401,
                    "AUTH.TOKEN_AUDIENCE_MISMATCH",
                    "当前登录入口不是普通用户投递入口");
        }
        return new CurrentMiniappDeliveryIdentity(
                actor.tenantCode(),
                actor.organizationCode(),
                new OrganizationUserUid(actor.principalUid()),
                actor.phoneBound(),
                new SessionUid(actor.sessionUid()),
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
                    "miniapp delivery identity query requires "
                            + "an existing read-only transaction");
        }
    }
}
