package org.enveloping.ecobin.operations.infrastructure.config;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Configuration;

/**
 * operations 模块显式装配边界。
 */
@Configuration(proxyBeanMethods = false)
@MapperScan("org.enveloping.ecobin.operations.infrastructure.persistence.mapper")
@EnableConfigurationProperties(ReliableTaskProperties.class)
public class OperationsModuleConfiguration {

    public OperationsModuleConfiguration(ReliableTaskProperties properties) {
        properties.validate();
    }
}
