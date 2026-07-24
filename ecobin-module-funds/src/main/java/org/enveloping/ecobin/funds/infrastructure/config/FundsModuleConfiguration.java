package org.enveloping.ecobin.funds.infrastructure.config;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.context.annotation.Configuration;

/**
 * funds 模块显式装配边界。
 */
@Configuration(proxyBeanMethods = false)
@MapperScan("org.enveloping.ecobin.funds.infrastructure.persistence.mapper")
public class FundsModuleConfiguration {
}
