package org.enveloping.ecobin.device.infrastructure.config;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.context.annotation.Configuration;

/**
 * device 模块显式装配边界。
 */
@Configuration(proxyBeanMethods = false)
@MapperScan("org.enveloping.ecobin.device.mapper")
public class DeviceModuleConfiguration {
}
