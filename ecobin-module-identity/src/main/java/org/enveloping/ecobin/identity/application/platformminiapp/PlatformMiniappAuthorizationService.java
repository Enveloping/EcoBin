package org.enveloping.ecobin.identity.application.platformminiapp;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.FactoryMiniappAuthorizationPort;
import org.enveloping.ecobin.identity.api.result.AuthorizedFactoryOperatorIdentity;
import org.enveloping.ecobin.identity.application.platformminiapp.PlatformMiniappRepository.FactoryOperatorRow;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronizationManager;

@Service
public class PlatformMiniappAuthorizationService
        implements FactoryMiniappAuthorizationPort {

    private final PlatformMiniappRepository repository;
    private final FactoryOperatorPersistenceRefFactory referenceFactory;

    PlatformMiniappAuthorizationService(
            PlatformMiniappRepository repository,
            FactoryOperatorPersistenceRefFactory referenceFactory) {
        this.repository = repository;
        this.referenceFactory = referenceFactory;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public AuthorizedFactoryOperatorIdentity requireCapability(
            String requiredCapability) {
        PlatformMiniappActor actor;
        try {
            actor = PlatformMiniappActorContext.required();
        } catch (IllegalStateException missingActor) {
            throw new TargetApiException(
                    401,
                    "AUTH.SESSION_INVALID",
                    "需要有效的厂家端会话");
        }
        FactoryOperatorRow current = repository.findFactoryOperator(
                        actor.factoryOperatorId(),
                        actor.factoryOperatorUid(),
                        !TransactionSynchronizationManager
                                .isCurrentTransactionReadOnly())
                .orElseThrow(
                        PlatformMiniappAuthorizationService::invalidSession);
        if (!current.enabled()
                || current.authVersion() != actor.authVersion()) {
            throw invalidSession();
        }
        if (requiredCapability == null
                || !actor.capabilities().contains(requiredCapability)) {
            throw new TargetApiException(
                    403,
                    "AUTH.CAPABILITY_REQUIRED",
                    "当前厂家操作员缺少所需权限");
        }
        return new AuthorizedFactoryOperatorIdentity(
                actor.factoryOperatorUid(),
                actor.operatorCode(),
                actor.sessionUid(),
                actor.displayName(),
                actor.capabilities(),
                referenceFactory.issue(current.id()));
    }

    private static TargetApiException invalidSession() {
        return new TargetApiException(
                401,
                "AUTH.SESSION_INVALID",
                "厂家操作员状态已经变化，请重新登录");
    }
}
