package org.enveloping.ecobin.identity.application.web;

import io.jsonwebtoken.JwtException;
import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.framework.security.JwtTokenProvider;
import org.enveloping.ecobin.framework.security.TargetWebSessionClaims;
import org.enveloping.ecobin.identity.infrastructure.persistence.v1.TargetIdentitySessionRepository;
import org.enveloping.ecobin.identity.infrastructure.persistence.v1.TargetIdentitySessionRepository.PlatformLoginPrincipal;
import org.enveloping.ecobin.identity.infrastructure.persistence.v1.TargetIdentitySessionRepository.StaffLoginPrincipal;
import org.enveloping.ecobin.identity.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.web.v1.auth.WebSessionView;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

import java.net.InetAddress;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;

@Service
public class TargetWebSessionService {

    public static final String COOKIE_NAME = "__Host-ecobin-web-session";
    public static final Duration SESSION_LIFETIME = Duration.ofHours(8);

    private static final int FAILURE_LIMIT = 5;
    private static final Duration LOCK_DURATION = Duration.ofMinutes(15);
    private static final String DUMMY_PASSWORD_HASH =
            "$2a$10$4lI4Vt97..D/ZV01YR/H2OpUwJalfktThtvArsxZUuzxa60dH5sPO";

    private final TargetIdentitySessionRepository repository;
    private final JwtTokenProvider tokenProvider;
    private final PasswordEncoder passwordEncoder;

    public TargetWebSessionService(
            TargetIdentitySessionRepository repository,
            JwtTokenProvider tokenProvider,
            PasswordEncoder passwordEncoder) {
        this.repository = repository;
        this.tokenProvider = tokenProvider;
        this.passwordEncoder = passwordEncoder;
    }

    @Transactional(isolation = Isolation.READ_COMMITTED, noRollbackFor = TargetApiException.class)
    public AuthenticatedWebSession loginPlatform(
            String loginName,
            String password,
            String existingToken,
            String remoteAddress,
            String userAgent) {
        String normalized = normalizeLogin(loginName);
        PlatformLoginPrincipal principal =
                repository.findPlatformLogin(normalized).orElse(null);
        String hash = principal == null
                ? DUMMY_PASSWORD_HASH : principal.passwordHash();
        if (!passwordEncoder.matches(password, hash)) {
            if (principal != null) {
                repository.recordPlatformLoginFailure(
                        principal.id(),
                        nextLock(principal.failedLoginCount()));
            }
            throw invalidCredentials();
        }
        if (!principal.enabled()
                || locked(principal.lockedUntil())) {
            throw unavailable();
        }
        revokeExisting(existingToken);
        Instant issuedAt = Instant.now().truncatedTo(ChronoUnit.SECONDS);
        Instant expiresAt = issuedAt.plus(SESSION_LIFETIME);
        UUID sessionUid = UUID.randomUUID();
        repository.createPlatformSession(
                principal,
                sessionUid,
                issuedAt,
                expiresAt,
                addressBytes(remoteAddress),
                digestUserAgent(userAgent));
        TargetWebSessionClaims claims = new TargetWebSessionClaims(
                principal.principalUid(),
                sessionUid,
                TrustedAudience.WEB_PLATFORM,
                issuedAt,
                expiresAt);
        TargetWebActor actor = repository.resolve(claims);
        return new AuthenticatedWebSession(
                tokenProvider.generateTargetWebToken(
                        claims.principalUid(),
                        claims.sessionUid(),
                        claims.audience(),
                        claims.issuedAt(),
                        claims.expiresAt()),
                WebSessionView.from(actor));
    }

    @Transactional(isolation = Isolation.READ_COMMITTED, noRollbackFor = TargetApiException.class)
    public AuthenticatedWebSession loginStaff(
            String loginName,
            String password,
            String existingToken,
            String remoteAddress,
            String userAgent) {
        String normalized = normalizeLogin(loginName);
        StaffLoginPrincipal principal =
                repository.findStaffLogin(normalized).orElse(null);
        String hash = principal == null
                ? DUMMY_PASSWORD_HASH : principal.passwordHash();
        if (!passwordEncoder.matches(password, hash)) {
            if (principal != null) {
                repository.recordStaffLoginFailure(
                        principal.id(),
                        nextLock(principal.failedLoginCount()));
            }
            throw invalidCredentials();
        }
        if (!principal.enabled()
                || !"ENABLED".equals(principal.tenantStatus())
                || locked(principal.lockedUntil())) {
            throw unavailable();
        }
        revokeExisting(existingToken);
        Instant issuedAt = Instant.now().truncatedTo(ChronoUnit.SECONDS);
        Instant expiresAt = issuedAt.plus(SESSION_LIFETIME);
        UUID sessionUid = UUID.randomUUID();
        repository.createStaffSession(
                principal,
                sessionUid,
                issuedAt,
                expiresAt,
                addressBytes(remoteAddress),
                digestUserAgent(userAgent));
        TargetWebSessionClaims claims = new TargetWebSessionClaims(
                principal.principalUid(),
                sessionUid,
                TrustedAudience.WEB_STAFF,
                issuedAt,
                expiresAt);
        TargetWebActor actor = repository.resolve(claims);
        return new AuthenticatedWebSession(
                tokenProvider.generateTargetWebToken(
                        claims.principalUid(),
                        claims.sessionUid(),
                        claims.audience(),
                        claims.issuedAt(),
                        claims.expiresAt()),
                WebSessionView.from(actor));
    }

    @Transactional(readOnly = true)
    public TargetWebActor resolve(String token) {
        try {
            return repository.resolve(
                    tokenProvider.parseTargetWebSession(token));
        } catch (TargetSessionRejectedException
                 | JwtException
                 | IllegalArgumentException exception) {
            throw new TargetApiException(
                    401,
                    "AUTH.SESSION_INVALID",
                    "登录状态无效，请重新登录",
                    false,
                    Map.of());
        }
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public void logout(TargetWebActor actor) {
        repository.revoke(actor, "USER_LOGOUT");
    }

    private void revokeExisting(String token) {
        if (token == null || token.isBlank()) {
            return;
        }
        TargetWebActor actor;
        try {
            actor = repository.resolve(
                    tokenProvider.parseTargetWebSession(token));
        } catch (TargetSessionRejectedException
                 | JwtException
                 | IllegalArgumentException ignored) {
            // An invalid/expired cookie cannot authorize anything and is
            // replaced by the newly authenticated session.
            return;
        }
        repository.revoke(actor, "REPLACED_BY_NEW_WEB_LOGIN");
    }

    private static String normalizeLogin(String value) {
        return value.trim().toLowerCase(Locale.ROOT);
    }

    private static boolean locked(Instant lockedUntil) {
        return lockedUntil != null && lockedUntil.isAfter(Instant.now());
    }

    private static Instant nextLock(int currentFailures) {
        return currentFailures + 1 >= FAILURE_LIMIT
                ? Instant.now().plus(LOCK_DURATION)
                : null;
    }

    private static byte[] addressBytes(String remoteAddress) {
        if (remoteAddress == null || remoteAddress.isBlank()) {
            return null;
        }
        try {
            return InetAddress.getByName(remoteAddress).getAddress();
        } catch (Exception ignored) {
            return null;
        }
    }

    private static byte[] digestUserAgent(String userAgent) {
        if (userAgent == null || userAgent.isBlank()) {
            return null;
        }
        try {
            return MessageDigest.getInstance("SHA-256").digest(
                    userAgent.getBytes(StandardCharsets.UTF_8));
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("SHA-256 is unavailable", impossible);
        }
    }

    private static TargetApiException invalidCredentials() {
        return new TargetApiException(
                401,
                "AUTH.INVALID_CREDENTIALS",
                "登录名或密码错误");
    }

    private static TargetApiException unavailable() {
        return new TargetApiException(
                403,
                "AUTH.ACCOUNT_UNAVAILABLE",
                "账号或所属租户当前不可登录");
    }

    public record AuthenticatedWebSession(
            String token,
            WebSessionView view) {
    }
}
