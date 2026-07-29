package org.enveloping.ecobin.operations.infrastructure.config;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Configuration;

/**
 * operations 模块显式装配边界。
 */
@Configuration(proxyBeanMethods = false)
@EnableConfigurationProperties(ReliableTaskProperties.class)
public class OperationsModuleConfiguration {

    public OperationsModuleConfiguration(ReliableTaskProperties properties) {
        properties.validate();
    }
}
