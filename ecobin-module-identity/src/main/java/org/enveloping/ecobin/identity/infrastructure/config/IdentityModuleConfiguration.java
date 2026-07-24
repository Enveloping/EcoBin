package org.enveloping.ecobin.identity.infrastructure.config;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.context.annotation.Configuration;

/**
 * identity 模块显式装配边界。
 */
@Configuration(proxyBeanMethods = false)
@MapperScan("org.enveloping.ecobin.identity.infrastructure.persistence.mapper")
public class IdentityModuleConfiguration {
}
