package org.enveloping.ecobin.identity.infrastructure.security;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.enveloping.ecobin.identity.application.web.TargetWebSessionService;
import org.enveloping.ecobin.identity.application.web.TargetWebRequestAuditService;
import org.enveloping.ecobin.framework.web.v1.TargetProblemDetail;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.annotation.Order;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.AnonymousAuthenticationFilter;
import org.springframework.security.web.csrf.CsrfFilter;
import org.springframework.security.web.csrf.CookieCsrfTokenRepository;
import org.springframework.security.web.csrf.CsrfException;
import org.springframework.security.web.csrf.CsrfTokenRequestAttributeHandler;
import tools.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.util.Map;

@Configuration(proxyBeanMethods = false)
public class TargetWebSecurityConfiguration {

    @Bean
    public CookieCsrfTokenRepository targetWebCsrfTokenRepository() {
        CookieCsrfTokenRepository repository =
                CookieCsrfTokenRepository.withHttpOnlyFalse();
        repository.setHeaderName("X-CSRF-TOKEN");
        repository.setCookieName("ecobin-csrf");
        repository.setCookieCustomizer(cookie -> cookie
                .path("/")
                .sameSite("Lax")
                .secure(true));
        return repository;
    }

    @Bean
    @Order(1)
    public SecurityFilterChain targetWebSecurityFilterChain(
            HttpSecurity http,
            TargetWebSessionService sessionService,
            TargetWebRequestAuditService auditService,
            ObjectMapper objectMapper,
            CookieCsrfTokenRepository csrfRepository,
            @Value("${ecobin.database.epoch.test-bypass:false}")
            boolean epochTestBypass) throws Exception {
        CsrfTokenRequestAttributeHandler csrfHandler =
                new CsrfTokenRequestAttributeHandler();
        csrfHandler.setCsrfRequestAttributeName(null);
        TargetWebAuthenticationFilter authenticationFilter =
                new TargetWebAuthenticationFilter(sessionService, objectMapper);

        http.securityMatcher("/api/v1/web/**")
                .sessionManagement(session -> session.sessionCreationPolicy(
                        SessionCreationPolicy.STATELESS))
                .csrf(csrf -> csrf
                        .csrfTokenRepository(csrfRepository)
                        .csrfTokenRequestHandler(csrfHandler))
                .requestCache(cache -> cache.disable())
                .formLogin(form -> form.disable())
                .httpBasic(basic -> basic.disable())
                .logout(logout -> logout.disable())
                .authorizeHttpRequests(authorize -> authorize
                        .requestMatchers(
                                HttpMethod.GET,
                                "/api/v1/web/auth/csrf-token")
                        .permitAll()
                        .requestMatchers(
                                HttpMethod.POST,
                                "/api/v1/web/auth/sessions",
                                "/api/v1/web/platform/auth/sessions")
                        .permitAll()
                        .requestMatchers("/api/v1/web/platform/**")
                        .hasAuthority("WEB_PLATFORM")
                        .requestMatchers("/api/v1/web/**")
                        .hasAuthority("WEB_STAFF")
                        .anyRequest().denyAll())
                .exceptionHandling(exceptions -> exceptions
                        .authenticationEntryPoint((request, response, cause) ->
                                writeProblem(
                                        objectMapper,
                                        request,
                                        response,
                                        401,
                                        "AUTH.SESSION_INVALID",
                                        "需要有效的 Web 会话"))
                        .accessDeniedHandler((request, response, cause) -> {
                            boolean csrf = cause instanceof CsrfException;
                            writeProblem(
                                    objectMapper,
                                    request,
                                    response,
                                    403,
                                    csrf
                                            ? "SECURITY.CSRF_INVALID"
                                            : "AUTH.CAPABILITY_REQUIRED",
                                    csrf
                                            ? "CSRF 校验失败"
                                            : "当前账号缺少所需能力");
                        }))
                .addFilterBefore(
                        authenticationFilter,
                        AnonymousAuthenticationFilter.class);
        if (!epochTestBypass) {
            http.addFilterBefore(
                    new TargetWebAuditFilter(auditService),
                    CsrfFilter.class);
        }
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
