package org.enveloping.ecobin.business.config;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.context.annotation.Configuration;

/**
 * F-03 前 legacy business 模块的显式装配边界。
 */
@Configuration(proxyBeanMethods = false)
@MapperScan("org.enveloping.ecobin.business.mapper")
public class LegacyBusinessModuleConfiguration {
}
