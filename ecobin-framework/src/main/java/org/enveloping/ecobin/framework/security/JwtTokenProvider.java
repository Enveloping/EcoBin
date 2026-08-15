package org.enveloping.ecobin.framework.security;

import io.jsonwebtoken.*;
import io.jsonwebtoken.security.Keys;
import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Date;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;

/**
 * JWT Token 工具类：生成、解析、校验
 */
@Component
public class JwtTokenProvider {

    private final SecretKey secretKey;

    public JwtTokenProvider(
            @Value("${jwt.secret:YOUR_DEFAULT_SECRET_KEY_MUST_BE_AT_LEAST_256_BITS_LONG_CHANGE_IN_PRODUCTION}")
            String secret) {
        this.secretKey = Keys.hmacShaKeyFor(secret.getBytes(StandardCharsets.UTF_8));
    }

    public String generateTargetWebToken(
            UUID principalUid,
            UUID sessionUid,
            TrustedAudience audience,
            Instant issuedAt,
            Instant expiresAt) {
        if (audience != TrustedAudience.WEB_PLATFORM
                && audience != TrustedAudience.WEB_STAFF) {
            throw new IllegalArgumentException("target Web token requires a Web audience");
        }
        return Jwts.builder()
                .issuer("ecobin")
                .subject(principalUid.toString())
                .id(sessionUid.toString())
                .audience()
                    .add(audienceName(audience))
                    .and()
                .issuedAt(Date.from(issuedAt))
                .expiration(Date.from(expiresAt))
                .signWith(secretKey)
                .compact();
    }

    public String generateTargetMiniappToken(
            UUID principalUid,
            UUID sessionUid,
            TrustedAudience audience,
            Instant issuedAt,
            Instant expiresAt) {
        if (audience != TrustedAudience.MINIAPP
                && audience != TrustedAudience.MINIAPP_STAFF) {
            throw new IllegalArgumentException(
                    "target miniapp token requires a miniapp audience");
        }
        return Jwts.builder()
                .issuer("ecobin")
                .subject(principalUid.toString())
                .id(sessionUid.toString())
                .audience()
                    .add(audienceName(audience))
                    .and()
                .issuedAt(Date.from(issuedAt))
                .expiration(Date.from(expiresAt))
                .signWith(secretKey)
                .compact();
    }

    public String generateTargetFactoryMiniappToken(
            UUID principalUid,
            UUID sessionUid,
            Instant issuedAt,
            Instant expiresAt) {
        return Jwts.builder()
                .issuer("ecobin")
                .subject(principalUid.toString())
                .id(sessionUid.toString())
                .audience()
                    .add(audienceName(TrustedAudience.MINIAPP_FACTORY))
                    .and()
                .issuedAt(Date.from(issuedAt))
                .expiration(Date.from(expiresAt))
                .signWith(secretKey)
                .compact();
    }

    public TargetWebSessionClaims parseTargetWebSession(String token) {
        Claims claims = parseClaims(token);
        if (!"ecobin".equals(claims.getIssuer())) {
            throw new IllegalArgumentException("target token issuer mismatch");
        }
        Set<String> audiences = claims.getAudience();
        if (audiences == null || audiences.size() != 1) {
            throw new IllegalArgumentException("target token requires one audience");
        }
        TrustedAudience audience = switch (audiences.iterator().next()) {
            case "web-platform" -> TrustedAudience.WEB_PLATFORM;
            case "web-staff" -> TrustedAudience.WEB_STAFF;
            default -> throw new IllegalArgumentException(
                    "target token audience mismatch");
        };
        if (claims.getSubject() == null
                || claims.getId() == null
                || claims.getIssuedAt() == null
                || claims.getExpiration() == null) {
            throw new IllegalArgumentException(
                    "target token is missing required claims");
        }
        return new TargetWebSessionClaims(
                UUID.fromString(claims.getSubject()),
                UUID.fromString(claims.getId()),
                audience,
                claims.getIssuedAt().toInstant(),
                claims.getExpiration().toInstant());
    }

    public TargetMiniappSessionClaims parseTargetMiniappSession(String token) {
        Claims claims = parseClaims(token);
        if (!"ecobin".equals(claims.getIssuer())) {
            throw new IllegalArgumentException("target token issuer mismatch");
        }
        Set<String> audiences = claims.getAudience();
        if (audiences == null || audiences.size() != 1) {
            throw new IllegalArgumentException(
                    "target token requires one audience");
        }
        TrustedAudience audience = switch (audiences.iterator().next()) {
            case "miniapp" -> TrustedAudience.MINIAPP;
            case "miniapp-staff" -> TrustedAudience.MINIAPP_STAFF;
            default -> throw new IllegalArgumentException(
                    "target token audience mismatch");
        };
        if (claims.getSubject() == null
                || claims.getId() == null
                || claims.getIssuedAt() == null
                || claims.getExpiration() == null) {
            throw new IllegalArgumentException(
                    "target token is missing required claims");
        }
        return new TargetMiniappSessionClaims(
                UUID.fromString(claims.getSubject()),
                UUID.fromString(claims.getId()),
                audience,
                claims.getIssuedAt().toInstant(),
                claims.getExpiration().toInstant());
    }

    public TargetFactoryMiniappSessionClaims
            parseTargetFactoryMiniappSession(String token) {
        Claims claims = parseClaims(token);
        if (!"ecobin".equals(claims.getIssuer())) {
            throw new IllegalArgumentException("target token issuer mismatch");
        }
        Set<String> audiences = claims.getAudience();
        if (audiences == null
                || audiences.size() != 1
                || !"miniapp-factory".equals(
                audiences.iterator().next())) {
            throw new IllegalArgumentException(
                    "target token audience mismatch");
        }
        if (claims.getSubject() == null
                || claims.getId() == null
                || claims.getIssuedAt() == null
                || claims.getExpiration() == null) {
            throw new IllegalArgumentException(
                    "target token is missing required claims");
        }
        return new TargetFactoryMiniappSessionClaims(
                UUID.fromString(claims.getSubject()),
                UUID.fromString(claims.getId()),
                claims.getIssuedAt().toInstant(),
                claims.getExpiration().toInstant());
    }

    private Claims parseClaims(String token) {
        return Jwts.parser()
                .verifyWith(secretKey)
                .build()
                .parseSignedClaims(token)
                .getPayload();
    }

    private static String audienceName(TrustedAudience audience) {
        return switch (audience) {
            case WEB_PLATFORM -> "web-platform";
            case WEB_STAFF -> "web-staff";
            case MINIAPP -> "miniapp";
            case MINIAPP_STAFF -> "miniapp-staff";
            case MINIAPP_FACTORY -> "miniapp-factory";
        };
    }

    public record TargetFactoryMiniappSessionClaims(
            UUID principalUid,
            UUID sessionUid,
            Instant issuedAt,
            Instant expiresAt) {

        public TargetFactoryMiniappSessionClaims {
            Objects.requireNonNull(principalUid, "principalUid");
            Objects.requireNonNull(sessionUid, "sessionUid");
            Objects.requireNonNull(issuedAt, "issuedAt");
            Objects.requireNonNull(expiresAt, "expiresAt");
            if (!expiresAt.isAfter(issuedAt)) {
                throw new IllegalArgumentException(
                        "expiresAt must be after issuedAt");
            }
        }
    }
}
