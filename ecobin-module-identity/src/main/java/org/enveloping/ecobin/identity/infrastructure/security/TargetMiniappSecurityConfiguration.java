package org.enveloping.ecobin.identity.infrastructure.security;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.enveloping.ecobin.identity.application.miniapp.TargetMiniappSessionService;
import org.enveloping.ecobin.framework.web.v1.TargetProblemDetail;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.annotation.Order;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.AnonymousAuthenticationFilter;
import tools.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.util.Map;

@Configuration(proxyBeanMethods = false)
public class TargetMiniappSecurityConfiguration {

    @Bean
    @Order(3)
    public SecurityFilterChain targetMiniappSecurityFilterChain(
            HttpSecurity http,
            TargetMiniappSessionService sessionService,
            ObjectMapper objectMapper) throws Exception {
        TargetMiniappAuthenticationFilter authenticationFilter =
                new TargetMiniappAuthenticationFilter(
                        sessionService, objectMapper);
        http.securityMatcher(
                        "/api/v1/miniapp/**",
                        "/api/v1/miniapp-staff/**")
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
                                "/api/v1/miniapp/auth/sessions")
                        .permitAll()
                        .requestMatchers("/api/v1/miniapp-staff/**")
                        .hasAuthority("MINIAPP_STAFF")
                        .requestMatchers("/api/v1/miniapp/**")
                        .hasAuthority("MINIAPP")
                        .anyRequest().denyAll())
                .exceptionHandling(exceptions -> exceptions
                        .authenticationEntryPoint((request, response, cause) ->
                                writeProblem(
                                        objectMapper,
                                        request,
                                        response,
                                        401,
                                        "AUTH.SESSION_INVALID",
                                        "需要有效的小程序会话"))
                        .accessDeniedHandler((request, response, cause) ->
                                writeProblem(
                                        objectMapper,
                                        request,
                                        response,
                                        403,
                                        "AUTH.CAPABILITY_REQUIRED",
                                        "当前小程序身份不能访问该入口")))
                .addFilterBefore(
                        authenticationFilter,
                        AnonymousAuthenticationFilter.class);
        return http.build();
    }

    private static void writeProblem(
            ObjectMapper objectMapper,
            HttpServletRequest request,
            HttpServletResponse response,
            int status,
            String code,
            String message) throws java.io.IOException {
        response.setStatus(status);
        response.setContentType(MediaType.APPLICATION_PROBLEM_JSON_VALUE);
        response.setCharacterEncoding(StandardCharsets.UTF_8.name());
        objectMapper.writeValue(
                response.getOutputStream(),
                new TargetProblemDetail(
                        code,
                        message,
                        TargetRequestIds.resolve(request),
                        false,
                        Map.of()));
    }
}
