package org.enveloping.ecobin.identity.application.startdelivery;

import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.framework.context.TrustedPrincipalKind;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.enveloping.ecobin.identity.api.id.SessionUid;
import org.enveloping.ecobin.identity.api.persistence.DeliverySessionOrganizationUserRef;
import org.enveloping.ecobin.identity.api.persistence.StartDeliveryOrganizationScopeRef;
import org.enveloping.ecobin.identity.api.persistence.StartDeliveryWalletOwnerRef;
import org.enveloping.ecobin.identity.api.port.StartDeliveryIdentityParticipationPort;
import org.enveloping.ecobin.identity.api.result.LockedDeliveryOrganizationUser;
import org.enveloping.ecobin.identity.api.result.LockedMiniappDeliveryScope;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappActor;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappActorContext;
import org.enveloping.ecobin.identity.application.persistence.StartDeliveryPersistenceRefFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.time.Instant;
import java.util.Objects;

@Service
public class StartDeliveryIdentityParticipationService
        implements StartDeliveryIdentityParticipationPort {

    private final StartDeliveryIdentityLockRepository repository;
    private final StartDeliveryPersistenceRefFactory referenceFactory;

    StartDeliveryIdentityParticipationService(
            StartDeliveryIdentityLockRepository repository,
            StartDeliveryPersistenceRefFactory referenceFactory) {
        this.repository = repository;
        this.referenceFactory = referenceFactory;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public LockedMiniappDeliveryScope lockCurrentMiniappScope() {
        requireActiveTransaction();
        TargetMiniappActor actor = requireOrdinaryDeliveryActor();
        ensureNoIdentityLockSequenceStarted();

        StartDeliveryIdentityLockRepository.TenantRow tenant =
                repository.lockTenant(actor.tenantId())
                        .orElseThrow(
                                StartDeliveryIdentityParticipationService
                                        ::invalidSession);
        requireSessionState(
                tenant.id() == actor.tenantId()
                        && tenant.tenantCode().equals(actor.tenantCode())
                        && "ENABLED".equals(tenant.status()));

        StartDeliveryIdentityLockRepository.OrganizationRow organization =
                repository.lockOrganization(actor.organizationId())
                        .orElseThrow(
                                StartDeliveryIdentityParticipationService
                                        ::invalidSession);
        requireSessionState(
                organization.id() == actor.organizationId()
                        && organization.tenantId() == tenant.id()
                        && organization.organizationCode().equals(
                                actor.organizationCode())
                        && "ENABLED".equals(organization.status()));

        StartDeliveryIdentityLockRepository.MiniappRow miniapp =
                repository.lockMiniapp(
                        actor.tenantId(),
                        actor.organizationId(),
                        actor.miniappChannelId())
                        .orElseThrow(
                                StartDeliveryIdentityParticipationService
                                        ::invalidSession);
        requireSessionState(
                miniapp.id() == actor.miniappChannelId()
                        && miniapp.tenantId() == tenant.id()
                        && miniapp.organizationId() == organization.id()
                        && miniapp.appId().equals(actor.appId())
                        && miniapp.loginEnabled()
                        && miniapp.activatedAt() != null);

        StartDeliveryOrganizationScopeRef scopeRef =
                referenceFactory.issueOrganizationScope(
                        tenant.id(),
                        organization.id());
        LockedMiniappDeliveryScope result =
                new LockedMiniappDeliveryScope(
                        tenant.tenantCode(),
                        organization.organizationCode(),
                        miniapp.appId(),
                        new SessionUid(actor.sessionUid()),
                        scopeRef);
        TransactionSynchronizationManager.registerSynchronization(
                new IdentityLockSequence(
                        result,
                        actor.tenantId(),
                        actor.organizationId(),
                        actor.miniappChannelId(),
                        actor.organizationUserId(),
                        actor.sessionUid()));
        return result;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public LockedDeliveryOrganizationUser lockCurrentOrganizationUser(
            LockedMiniappDeliveryScope lockedScope) {
        requireActiveTransaction();
        Objects.requireNonNull(lockedScope, "lockedScope");
        IdentityLockSequence sequence = requireLockSequence(lockedScope);
        sequence.claimUserPhase();

        TargetMiniappActor actor = requireOrdinaryDeliveryActor();
        requireSessionState(sequence.matches(actor));

        StartDeliveryIdentityLockRepository.OrganizationUserRow user =
                repository.lockOrganizationUser(
                                actor.organizationUserId())
                        .orElseThrow(
                                StartDeliveryIdentityParticipationService
                                        ::invalidSession);
        requireSessionState(
                user.id() == actor.organizationUserId()
                        && user.userUid().equals(actor.principalUid())
                        && user.tenantId() == actor.tenantId()
                        && user.organizationId() == actor.organizationId()
                        && user.miniappId()
                        == actor.miniappChannelId()
                        && "ACTIVE".equals(user.status())
                        && user.authVersion() == actor.authVersion());

        Instant now = Instant.now();
        StartDeliveryIdentityLockRepository.OrganizationUserSessionRow
                session = repository.lockOrganizationUserSession(
                                actor.sessionUid())
                        .orElseThrow(
                                StartDeliveryIdentityParticipationService
                                        ::invalidSession);
        requireSessionState(
                session.sessionUid().equals(actor.sessionUid())
                        && session.tenantId() == user.tenantId()
                        && session.organizationId()
                        == user.organizationId()
                        && session.miniappId() == user.miniappId()
                        && session.organizationUserId() == user.id()
                        && !session.issuedAt().isAfter(now)
                        && session.expiresAt().isAfter(now)
                        && session.revokedAt() == null
                        && session.authVersionSnapshot()
                        == user.authVersion()
                        && session.expiresAt().equals(actor.expiresAt()));

        if (user.phoneE164() == null || user.phoneBoundAt() == null) {
            throw new TargetApiException(
                    422,
                    "IDENTITY.PHONE_BINDING_REQUIRED",
                    "开始投递前必须先绑定手机号");
        }

        StartDeliveryWalletOwnerRef walletOwnerRef =
                referenceFactory.issueWalletOwner(
                        user.tenantId(),
                        user.organizationId(),
                        user.id());
        DeliverySessionOrganizationUserRef sessionUserRef =
                referenceFactory.issueDeliverySessionUser(
                        user.tenantId(),
                        user.organizationId(),
                        user.id());
        var auditActorRef =
                referenceFactory.issueAuditActor(
                        user.tenantId(),
                        user.organizationId(),
                        user.id());
        return new LockedDeliveryOrganizationUser(
                new OrganizationUserUid(user.userUid()),
                new SessionUid(session.sessionUid()),
                walletOwnerRef,
                sessionUserRef,
                auditActorRef);
    }

    private static TargetMiniappActor requireOrdinaryDeliveryActor() {
        TargetMiniappActor actor = TargetMiniappActorContext.required();
        if (actor.audience() != TrustedAudience.MINIAPP
                || actor.principalKind()
                != TrustedPrincipalKind.ORGANIZATION_USER
                || !"USER".equals(actor.entryMode())) {
            throw new TargetApiException(
                    401,
                    "AUTH.TOKEN_AUDIENCE_MISMATCH",
                    "当前登录入口不能开始普通用户投递");
        }
        return actor;
    }

    private static void requireActiveTransaction() {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()
                || !TransactionSynchronizationManager
                .isSynchronizationActive()) {
            throw new IllegalStateException(
                    "start-delivery identity participation "
                            + "requires an existing transaction");
        }
    }

    private static void ensureNoIdentityLockSequenceStarted() {
        boolean alreadyStarted =
                TransactionSynchronizationManager.getSynchronizations()
                        .stream()
                        .anyMatch(IdentityLockSequence.class::isInstance);
        if (alreadyStarted) {
            throw new IllegalStateException(
                    "start-delivery identity lock sequence "
                            + "was already started");
        }
    }

    private static IdentityLockSequence requireLockSequence(
            LockedMiniappDeliveryScope lockedScope) {
        return TransactionSynchronizationManager.getSynchronizations()
                .stream()
                .filter(IdentityLockSequence.class::isInstance)
                .map(IdentityLockSequence.class::cast)
                .filter(sequence ->
                        sequence.lockedScope() == lockedScope)
                .findFirst()
                .orElseThrow(() -> new IllegalStateException(
                        "organization user lock requires the scope "
                                + "locked earlier in this transaction"));
    }

    private static void requireSessionState(boolean valid) {
        if (!valid) {
            throw invalidSession();
        }
    }

    private static TargetApiException invalidSession() {
        return new TargetApiException(
                401,
                "AUTH.SESSION_INVALID",
                "小程序登录状态无效，请重新登录");
    }

    private static final class IdentityLockSequence
            implements TransactionSynchronization {

        private final LockedMiniappDeliveryScope lockedScope;
        private final long tenantId;
        private final long organizationId;
        private final long miniappId;
        private final long organizationUserId;
        private final java.util.UUID sessionUid;
        private boolean userPhaseClaimed;

        private IdentityLockSequence(
                LockedMiniappDeliveryScope lockedScope,
                long tenantId,
                long organizationId,
                long miniappId,
                long organizationUserId,
                java.util.UUID sessionUid) {
            this.lockedScope = lockedScope;
            this.tenantId = tenantId;
            this.organizationId = organizationId;
            this.miniappId = miniappId;
            this.organizationUserId = organizationUserId;
            this.sessionUid = sessionUid;
        }

        private LockedMiniappDeliveryScope lockedScope() {
            return lockedScope;
        }

        private boolean matches(TargetMiniappActor actor) {
            return actor.tenantId() == tenantId
                    && actor.organizationId() == organizationId
                    && actor.miniappChannelId() == miniappId
                    && actor.organizationUserId() == organizationUserId
                    && actor.sessionUid().equals(sessionUid);
        }

        private void claimUserPhase() {
            if (userPhaseClaimed) {
                throw new IllegalStateException(
                        "organization user lock phase was already consumed");
            }
            userPhaseClaimed = true;
        }
    }
}
