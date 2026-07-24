package org.enveloping.ecobin.recycling.infrastructure.config;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.context.annotation.Configuration;

/**
 * recycling 模块显式装配边界。
 */
@Configuration(proxyBeanMethods = false)
@MapperScan("org.enveloping.ecobin.recycling.infrastructure.persistence.mapper")
public class RecyclingModuleConfiguration {
}
