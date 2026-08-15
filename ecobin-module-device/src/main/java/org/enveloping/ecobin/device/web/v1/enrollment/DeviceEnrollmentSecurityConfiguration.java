package org.enveloping.ecobin.device.web.v1.enrollment;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.annotation.Order;
import org.springframework.http.HttpMethod;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.web.SecurityFilterChain;

/** The only unauthenticated factory API; its request authenticates itself. */
@Configuration(proxyBeanMethods = false)
public class DeviceEnrollmentSecurityConfiguration {

    @Bean
    @Order(-1)
    public SecurityFilterChain deviceEnrollmentSecurityFilterChain(
            HttpSecurity http) throws Exception {
        http.securityMatcher(
                        "/api/v1/device-enrollment/challenges",
                        "/api/v1/device-enrollments")
                .sessionManagement(session -> session.sessionCreationPolicy(
                        SessionCreationPolicy.STATELESS))
                .csrf(AbstractHttpConfigurer::disable)
                .requestCache(cache -> cache.disable())
                .formLogin(AbstractHttpConfigurer::disable)
                .httpBasic(AbstractHttpConfigurer::disable)
                .logout(AbstractHttpConfigurer::disable)
                .authorizeHttpRequests(authorize -> authorize
                        .requestMatchers(
                                HttpMethod.POST,
                                "/api/v1/device-enrollment/challenges",
                                "/api/v1/device-enrollments")
                        .permitAll()
                        .anyRequest().denyAll());
        return http.build();
    }
}
