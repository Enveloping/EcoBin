package org.enveloping.ecobin.identity.infrastructure.security;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.enveloping.ecobin.framework.web.v1.TargetProblemDetail;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.identity.application.platformminiapp.PlatformMiniappSessionService;
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
public class PlatformMiniappSecurityConfiguration {

    @Bean
    @Order(2)
    public SecurityFilterChain platformMiniappSecurityFilterChain(
            HttpSecurity http,
            PlatformMiniappSessionService sessionService,
            ObjectMapper objectMapper) throws Exception {
        PlatformMiniappAuthenticationFilter authenticationFilter =
                new PlatformMiniappAuthenticationFilter(
                        sessionService, objectMapper);
        http.securityMatcher("/api/v1/miniapp-factory/**")
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
                                "/api/v1/miniapp-factory/auth/sessions")
                        .permitAll()
                        .requestMatchers("/api/v1/miniapp-factory/**")
                        .hasAuthority("MINIAPP_FACTORY")
                        .anyRequest().denyAll())
                .exceptionHandling(exceptions -> exceptions
                        .authenticationEntryPoint((request, response, cause) ->
                                writeProblem(
                                        objectMapper,
                                        request,
                                        response,
                                        401,
                                        "AUTH.SESSION_INVALID",
                                        "需要有效的厂家端会话"))
                        .accessDeniedHandler((request, response, cause) ->
                                writeProblem(
                                        objectMapper,
                                        request,
                                        response,
                                        403,
                                        "AUTH.CAPABILITY_REQUIRED",
                                        "当前厂家操作员身份不能访问该入口")))
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
