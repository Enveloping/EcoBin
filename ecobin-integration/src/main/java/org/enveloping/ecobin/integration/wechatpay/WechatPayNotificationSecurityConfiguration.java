package org.enveloping.ecobin.integration.wechatpay;

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.annotation.Order;
import org.springframework.http.HttpMethod;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.web.SecurityFilterChain;

@Configuration(proxyBeanMethods = false)
@ConditionalOnProperty(
        prefix = "ecobin.external", name = "mode", havingValue = "real")
public class WechatPayNotificationSecurityConfiguration {

    @Bean
    @Order(0)
    public SecurityFilterChain wechatPayNotificationSecurityFilterChain(
            HttpSecurity http) throws Exception {
        http.securityMatcher("/api/v1/wechat-pay/notifications/**")
                .csrf(AbstractHttpConfigurer::disable)
                .sessionManagement(session -> session.sessionCreationPolicy(
                        SessionCreationPolicy.STATELESS))
                .requestCache(cache -> cache.disable())
                .formLogin(AbstractHttpConfigurer::disable)
                .httpBasic(AbstractHttpConfigurer::disable)
                .logout(AbstractHttpConfigurer::disable)
                .authorizeHttpRequests(auth -> auth
                        .requestMatchers(
                                HttpMethod.POST,
                                "/api/v1/wechat-pay/notifications/**")
                        .permitAll()
                        .anyRequest().denyAll());
        return http.build();
    }
}
