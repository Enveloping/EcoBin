package org.enveloping.ecobin.identity.application.startdelivery;

import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.framework.context.TrustedPrincipalKind;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.enveloping.ecobin.identity.api.id.SessionUid;
import org.enveloping.ecobin.identity.api.port.StartCleanIdentityParticipationPort;
import org.enveloping.ecobin.identity.api.result.LockedCleanOrganizationUser;
import org.enveloping.ecobin.identity.api.result.LockedMiniappCleanScope;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappActor;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappActorContext;
import org.enveloping.ecobin.identity.application.persistence.StartCleanPersistenceRefFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.time.Instant;
import java.util.Objects;

/**
 * 复核当前小程序会话确实属于已授予清运能力的机构用户。
 */
@Service
public class StartCleanIdentityParticipationService
        implements StartCleanIdentityParticipationPort {

    private static final String CLEAN_CAPABILITY = "clean.operation";

    private final StartDeliveryIdentityLockRepository repository;
    private final StartCleanPersistenceRefFactory referenceFactory;

    StartCleanIdentityParticipationService(
            StartDeliveryIdentityLockRepository repository,
            StartCleanPersistenceRefFactory referenceFactory) {
        this.repository = repository;
        this.referenceFactory = referenceFactory;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public LockedMiniappCleanScope lockCurrentMiniappScope() {
        requireActiveTransaction();
        TargetMiniappActor actor = requireCleanerActor();
        ensureNoCleanLockSequenceStarted();

        var tenant = repository.lockTenant(actor.tenantId())
                .orElseThrow(
                        StartCleanIdentityParticipationService
                                ::invalidSession);
        requireSessionState(
                tenant.id() == actor.tenantId()
                        && tenant.tenantCode().equals(
                        actor.tenantCode())
                        && "ENABLED".equals(tenant.status()));

        var organization = repository.lockOrganization(
                        actor.organizationId())
                .orElseThrow(
                        StartCleanIdentityParticipationService
                                ::invalidSession);
        requireSessionState(
                organization.id() == actor.organizationId()
                        && organization.tenantId() == tenant.id()
                        && organization.organizationCode().equals(
                        actor.organizationCode())
                        && "ENABLED".equals(
                        organization.status()));

        var miniapp = repository.lockMiniapp(
                        actor.tenantId(),
                        actor.organizationId(),
                        actor.miniappChannelId())
                .orElseThrow(
                        StartCleanIdentityParticipationService
                                ::invalidSession);
        requireSessionState(
                miniapp.id() == actor.miniappChannelId()
                        && miniapp.tenantId() == tenant.id()
                        && miniapp.organizationId()
                        == organization.id()
                        && miniapp.appId().equals(actor.appId())
                        && miniapp.loginEnabled()
                        && miniapp.activatedAt() != null);

        LockedMiniappCleanScope result = new LockedMiniappCleanScope(
                tenant.tenantCode(),
                organization.organizationCode(),
                miniapp.appId(),
                new SessionUid(actor.sessionUid()),
                referenceFactory.issueOrganizationScope(
                        tenant.id(), organization.id()));
        TransactionSynchronizationManager.registerSynchronization(
                new CleanIdentityLockSequence(
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
    public LockedCleanOrganizationUser lockCurrentCleaner(
            LockedMiniappCleanScope lockedScope) {
        requireActiveTransaction();
        Objects.requireNonNull(lockedScope, "lockedScope");
        CleanIdentityLockSequence sequence = requireLockSequence(
                lockedScope);
        sequence.claimUserPhase();

        TargetMiniappActor actor = requireCleanerActor();
        requireSessionState(sequence.matches(actor));
        var user = repository.lockOrganizationUser(
                        actor.organizationUserId())
                .orElseThrow(
                        StartCleanIdentityParticipationService
                                ::invalidSession);
        requireSessionState(
                user.id() == actor.organizationUserId()
                        && user.userUid().equals(actor.principalUid())
                        && user.tenantId() == actor.tenantId()
                        && user.organizationId()
                        == actor.organizationId()
                        && user.miniappId()
                        == actor.miniappChannelId()
                        && "ACTIVE".equals(user.status())
                        && user.authVersion() == actor.authVersion());

        Instant now = Instant.now();
        var session = repository.lockOrganizationUserSession(
                        actor.sessionUid())
                .orElseThrow(
                        StartCleanIdentityParticipationService
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
                        && session.expiresAt().equals(
                        actor.expiresAt()));

        return new LockedCleanOrganizationUser(
                new OrganizationUserUid(user.userUid()),
                new SessionUid(session.sessionUid()),
                referenceFactory.issueOrganizationUser(
                        user.tenantId(),
                        user.organizationId(),
                        user.id()),
                referenceFactory.issueAuditActor(
                        user.tenantId(),
                        user.organizationId(),
                        user.id()));
    }

    private static TargetMiniappActor requireCleanerActor() {
        TargetMiniappActor actor = TargetMiniappActorContext.required();
        if (actor.audience() != TrustedAudience.MINIAPP
                || actor.principalKind()
                != TrustedPrincipalKind.ORGANIZATION_USER
                || !"CLEANING".equals(actor.entryMode())
                || !actor.capabilities().contains(CLEAN_CAPABILITY)) {
            throw new TargetApiException(
                    403,
                    "AUTH.CAPABILITY_REQUIRED",
                    "当前小程序用户没有清运能力");
        }
        return actor;
    }

    private static void requireActiveTransaction() {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()
                || !TransactionSynchronizationManager
                .isSynchronizationActive()) {
            throw new IllegalStateException(
                    "start-clean identity participation requires an existing transaction");
        }
    }

    private static void ensureNoCleanLockSequenceStarted() {
        boolean started = TransactionSynchronizationManager
                .getSynchronizations()
                .stream()
                .anyMatch(CleanIdentityLockSequence.class::isInstance);
        if (started) {
            throw new IllegalStateException(
                    "start-clean identity lock sequence was already started");
        }
    }

    private static CleanIdentityLockSequence requireLockSequence(
            LockedMiniappCleanScope scope) {
        return TransactionSynchronizationManager.getSynchronizations()
                .stream()
                .filter(CleanIdentityLockSequence.class::isInstance)
                .map(CleanIdentityLockSequence.class::cast)
                .filter(sequence -> sequence.lockedScope() == scope)
                .findFirst()
                .orElseThrow(() -> new IllegalStateException(
                        "cleaner lock requires the scope locked earlier in this transaction"));
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
                "清运小程序登录状态无效，请重新登录");
    }

    private static final class CleanIdentityLockSequence
            implements TransactionSynchronization {

        private final LockedMiniappCleanScope lockedScope;
        private final long tenantId;
        private final long organizationId;
        private final long miniappId;
        private final long organizationUserId;
        private final java.util.UUID sessionUid;
        private boolean userPhaseClaimed;

        private CleanIdentityLockSequence(
                LockedMiniappCleanScope lockedScope,
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

        private LockedMiniappCleanScope lockedScope() {
            return lockedScope;
        }

        private boolean matches(TargetMiniappActor actor) {
            return actor.tenantId() == tenantId
                    && actor.organizationId() == organizationId
                    && actor.miniappChannelId() == miniappId
                    && actor.organizationUserId()
                    == organizationUserId
                    && actor.sessionUid().equals(sessionUid);
        }

        private void claimUserPhase() {
            if (userPhaseClaimed) {
                throw new IllegalStateException(
                        "cleaner lock phase was already consumed");
            }
            userPhaseClaimed = true;
        }
    }
}
