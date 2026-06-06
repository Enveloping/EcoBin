package org.enveloping.ecobin.framework.config;

import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.framework.security.JwtAuthenticationFilter;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpMethod;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;

/**
 * Spring Security 配置
 */
@Configuration
@EnableWebSecurity
@RequiredArgsConstructor
public class SecurityConfig {

    private final JwtAuthenticationFilter jwtAuthenticationFilter;

    @Bean
    public SecurityFilterChain securityFilterChain(HttpSecurity http) throws Exception {
        http
                .csrf(AbstractHttpConfigurer::disable)
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .authorizeHttpRequests(auth -> auth
                        // 认证接口放行
                        .requestMatchers("/api/system/auth/**").permitAll()

                        // 平台管理：管理员账号（仅超管）
                        .requestMatchers("/api/system/admin/**").hasRole("SUPER_ADMIN")
                        // 租户自查自身资料（仅租户本人）；须先于下方通配规则声明
                        .requestMatchers("/api/system/tenant/me").hasRole("TENANT")
                        // 平台管理：租户 CRUD（超管 + 管理员）
                        .requestMatchers("/api/system/tenant/**").hasAnyRole("SUPER_ADMIN", "ADMIN")
                        // 用户管理：租户管自己租户用户；超管全量查看
                        .requestMatchers("/api/system/user/**").hasAnyRole("SUPER_ADMIN", "TENANT")

                        // 设备 / 投口（含 door 子路径）：超管 + 管理员 + 租户；租户仅能动自己设备（数据隔离兜底）
                        .requestMatchers("/api/device/**").hasAnyRole("SUPER_ADMIN", "ADMIN", "TENANT")

                        // 投递订单：超管 + 租户
                        .requestMatchers("/api/business/delivery/**").hasAnyRole("SUPER_ADMIN", "TENANT")

                        // 清运订单——读（超管 + 租户）
                        .requestMatchers(HttpMethod.GET, "/api/business/clean/**")
                        .hasAnyRole("SUPER_ADMIN", "TENANT")
                        // 清运订单——写（创建清运单）：租户 + 设备管理员 + 清运员
                        .requestMatchers("/api/business/clean/**").hasAnyRole("TENANT", "CLEANER", "DEVICE_ADMIN")

                        // 统计：业务数据视图，超管 + 租户
                        .requestMatchers("/api/statistics/**").hasAnyRole("SUPER_ADMIN", "TENANT")

                        // 设备 IoT 上报接口：明文 SN 信任，无用户登录态；鉴权由服务层按 SN 反查设备校验
                        .requestMatchers("/api/iot/**").permitAll()

                        // 小程序终端用户 C 端接口：仅访问属于自己的数据（投递记录 / 个人信息）
                        // 清运员/设备管理员本身也是小程序用户，同样拥有自己的记录，故三角色均放行
                        .requestMatchers("/api/app/**").hasAnyRole("USER", "CLEANER", "DEVICE_ADMIN")

                        // 其余接口需登录
                        .anyRequest().authenticated()
                )
                .addFilterBefore(jwtAuthenticationFilter, UsernamePasswordAuthenticationFilter.class);

        return http.build();
    }

    @Bean
    public PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder();
    }
}
