package org.enveloping.ecobin.operations.infrastructure.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;

/** Enables reliable and governance schedules in both fake and real modes. */
@Configuration(proxyBeanMethods = false)
@EnableScheduling
public class OperationsSchedulingConfiguration {
}
