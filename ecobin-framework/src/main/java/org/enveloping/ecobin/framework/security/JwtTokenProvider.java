package org.enveloping.ecobin.framework.security;

import io.jsonwebtoken.*;
import io.jsonwebtoken.security.Keys;
import org.enveloping.ecobin.common.enums.UserRole;
import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Date;
import java.util.UUID;

/**
 * JWT Token 工具类：生成、解析、校验
 */
@Component
public class JwtTokenProvider {

    private final SecretKey secretKey;

    /** 默认有效期：24小时 */
    private final long expiration;

    public JwtTokenProvider(@Value("${jwt.secret:YOUR_DEFAULT_SECRET_KEY_MUST_BE_AT_LEAST_256_BITS_LONG_CHANGE_IN_PRODUCTION}") String secret,
                             @Value("${jwt.expiration:86400000}") long expiration) {
        this.secretKey = Keys.hmacShaKeyFor(secret.getBytes(StandardCharsets.UTF_8));
        this.expiration = expiration;
    }

    /**
     * 生成 JWT Token
     *
     * @param userId   主体ID（adminId / tenantId 自身 / userId）
     * @param subject  subject（admin username / tenant username / user openid）
     * @param tenantId 租户ID（平台域固定为平台池ID）
     * @param role     角色值（9/8/7/3/2/1）
     * @return JWT Token 字符串
     */
    public String generateToken(Long userId, String subject, Long tenantId, Integer role) {
        Date now = new Date();
        Date expiryDate = new Date(now.getTime() + expiration);
        TrustedAudience audience = audienceOf(role);

        return Jwts.builder()
                .issuer("ecobin")
                .id(UUID.randomUUID().toString())
                .subject(subject)
                .claim("userId", userId)
                .claim("tenantId", tenantId)
                .claim("role", role)
                .claim("clientAudience", audience.name())
                .claim("authVersion", 0L)
                .issuedAt(now)
                .expiration(expiryDate)
                .signWith(secretKey)
                .compact();
    }

    public JwtSessionClaims parseSession(String token) {
        Claims claims = parseClaims(token);
        String jti = claims.getId();
        String audience = claims.get("clientAudience", String.class);
        if (jti == null || audience == null) {
            throw new IllegalArgumentException("token is missing typed session claims");
        }
        return new JwtSessionClaims(
                claims.get("userId", Long.class),
                claims.get("tenantId", Long.class),
                claims.get("role", Integer.class),
                claims.getSubject(),
                UUID.fromString(jti),
                TrustedAudience.valueOf(audience),
                claims.getIssuedAt().toInstant());
    }

    /**
     * 从 Token 中获取用户名
     */
    public String getUsername(String token) {
        return parseClaims(token).getSubject();
    }

    /**
     * 从 Token 中获取用户ID
     */
    public Long getUserId(String token) {
        return parseClaims(token).get("userId", Long.class);
    }

    /**
     * 从 Token 中获取租户ID
     */
    public Long getTenantId(String token) {
        return parseClaims(token).get("tenantId", Long.class);
    }

    /**
     * 从 Token 中获取角色值
     */
    public Integer getRole(String token) {
        return parseClaims(token).get("role", Integer.class);
    }

    /**
     * 从 Token 中获取签发时间
     */
    public Date getIssuedAt(String token) {
        return parseClaims(token).getIssuedAt();
    }

    /**
     * 校验 Token 是否有效
     */
    public boolean validateToken(String token) {
        try {
            parseClaims(token);
            return true;
        } catch (JwtException | IllegalArgumentException e) {
            return false;
        }
    }

    private Claims parseClaims(String token) {
        return Jwts.parser()
                .verifyWith(secretKey)
                .build()
                .parseSignedClaims(token)
                .getPayload();
    }

    private static TrustedAudience audienceOf(Integer role) {
        if (UserRole.isPlatform(role)) {
            return TrustedAudience.WEB_PLATFORM;
        }
        if (role != null && role == UserRole.TENANT.getCode()) {
            return TrustedAudience.WEB_STAFF;
        }
        return TrustedAudience.MINIAPP;
    }
}
