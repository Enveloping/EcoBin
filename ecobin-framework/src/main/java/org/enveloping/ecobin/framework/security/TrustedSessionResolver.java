package org.enveloping.ecobin.framework.security;

/**
 * framework 定义、identity 实现的服务端会话/数据库事实解析端口。
 */
public interface TrustedSessionResolver {

    ResolvedTrustedSession resolve(JwtSessionClaims claims);
}
