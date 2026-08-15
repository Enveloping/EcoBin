package org.enveloping.ecobin.identity.application.platformminiapp;

import org.enveloping.ecobin.framework.security.JwtTokenProvider;
import org.enveloping.ecobin.framework.security.JwtTokenProvider.TargetFactoryMiniappSessionClaims;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.application.platformminiapp.PlatformMiniappRepository.FactoryMiniappSessionRow;
import org.enveloping.ecobin.identity.web.v1.miniapp.FactoryMiniappModels.FactoryMiniappSessionView;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Set;

@Service
public class PlatformMiniappSessionService {

    private static final Set<String> CAPABILITIES = Set.copyOf(
            PlatformMiniappLoginTransactionService.CAPABILITIES);

    private final PlatformMiniappRepository repository;
    private final JwtTokenProvider tokenProvider;

    PlatformMiniappSessionService(
            PlatformMiniappRepository repository,
            JwtTokenProvider tokenProvider) {
        this.repository = repository;
        this.tokenProvider = tokenProvider;
    }

    @Transactional(readOnly = true)
    public PlatformMiniappActor resolve(String token) {
        TargetFactoryMiniappSessionClaims claims;
        try {
            claims = tokenProvider.parseTargetFactoryMiniappSession(token);
        } catch (RuntimeException invalidToken) {
            throw rejected();
        }
        FactoryMiniappSessionRow row = repository.findSession(
                        claims.sessionUid())
                .orElseThrow(PlatformMiniappSessionService::rejected);
        require(row.sessionUid().equals(claims.sessionUid()));
        require(row.factoryOperatorUid().equals(claims.principalUid()));
        require(row.issuedAt().equals(claims.issuedAt()));
        require(row.expiresAt().equals(claims.expiresAt()));
        require(row.expiresAt().isAfter(Instant.now()));
        require(row.revokedAt() == null);
        require(row.authVersionSnapshot()
                == row.factoryOperatorAuthVersion());
        require(row.factoryOperatorEnabled());
        require("ACTIVE".equals(row.bindingStatus()));
        require(row.bindingRevokedAt() == null);
        require(row.channelLoginEnabled());
        require(row.channelActivatedAt() != null);
        require("ACTIVE".equals(row.subjectStatus()));
        return new PlatformMiniappActor(
                row.factoryOperatorId(),
                row.factoryOperatorUid(),
                row.operatorCode(),
                row.sessionUid(),
                row.factoryOperatorAuthVersion(),
                row.expiresAt(),
                row.displayName(),
                CAPABILITIES);
    }

    public FactoryMiniappSessionView view(PlatformMiniappActor actor) {
        return new FactoryMiniappSessionView(
                PlatformMiniappLoginTransactionService.AUDIENCE,
                PlatformMiniappLoginTransactionService.ENTRY_MODE,
                actor.expiresAt(),
                actor.factoryOperatorUid(),
                actor.operatorCode(),
                actor.displayName(),
                actor.capabilities().stream().sorted().toList());
    }

    @Transactional
    public void revokeCurrent(PlatformMiniappActor actor) {
        repository.revokeSession(
                actor.sessionUid(),
                actor.factoryOperatorId(),
                Instant.now().truncatedTo(ChronoUnit.MILLIS),
                "LOGOUT");
    }

    private static void require(boolean condition) {
        if (!condition) {
            throw rejected();
        }
    }

    private static TargetApiException rejected() {
        return new TargetApiException(
                401,
                "AUTH.SESSION_INVALID",
                "厂家端登录状态无效，请重新登录");
    }
}
