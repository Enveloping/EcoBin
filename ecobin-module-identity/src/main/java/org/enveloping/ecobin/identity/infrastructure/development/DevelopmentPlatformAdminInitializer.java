package org.enveloping.ecobin.identity.infrastructure.development;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.core.env.Environment;
import org.springframework.core.env.Profiles;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.temporal.ChronoUnit;
import java.util.Locale;
import java.util.UUID;
import java.util.regex.Pattern;

/**
 * Creates one known platform administrator for local development.
 *
 * <p>The initializer only acts when the platform administrator table is
 * empty. It never changes or resets an existing account. REAL external mode
 * and production profiles reject an enabled initializer before any database
 * write.</p>
 */
@Component
@ConditionalOnProperty(
        prefix = "ecobin.development.default-platform-admin",
        name = "enabled",
        havingValue = "true")
public final class DevelopmentPlatformAdminInitializer
        implements ApplicationRunner {

    private static final Logger LOGGER =
            LoggerFactory.getLogger(
                    DevelopmentPlatformAdminInitializer.class);
    private static final Pattern LOGIN_NAME =
            Pattern.compile("^[a-z0-9][a-z0-9._-]{0,63}$");

    private final JdbcTemplate jdbc;
    private final PasswordEncoder passwordEncoder;
    private final DevelopmentPlatformAdminProperties properties;
    private final Environment environment;

    public DevelopmentPlatformAdminInitializer(
            JdbcTemplate jdbc,
            PasswordEncoder passwordEncoder,
            DevelopmentPlatformAdminProperties properties,
            Environment environment) {
        this.jdbc = jdbc;
        this.passwordEncoder = passwordEncoder;
        this.properties = properties;
        this.environment = environment;
    }

    @Override
    public void run(ApplicationArguments arguments) {
        requireDevelopmentMode();
        validateConfiguration();
        if (platformAdminCount() > 0) {
            LOGGER.info(
                    "Development platform administrator bootstrap skipped: "
                            + "an administrator already exists");
            return;
        }

        LocalDateTime now = LocalDateTime.now(ZoneOffset.UTC)
                .truncatedTo(ChronoUnit.MILLIS);
        try {
            int inserted = jdbc.update("""
                            INSERT INTO iam_platform_admin (
                                platform_admin_uid, login_name,
                                password_hash, display_name, enabled,
                                failed_login_count, locked_until,
                                auth_version, password_changed_at,
                                lock_version, created_at, updated_at
                            ) VALUES (
                                ?, ?, ?, ?, 1,
                                0, NULL,
                                0, ?,
                                0, ?, ?
                            )
                            """,
                    UUID.randomUUID().toString(),
                    properties.getLoginName(),
                    passwordEncoder.encode(properties.getPassword()),
                    properties.getDisplayName().trim(),
                    now,
                    now,
                    now);
            if (inserted != 1) {
                throw new IllegalStateException(
                        "development platform administrator was not created");
            }
        } catch (DuplicateKeyException exception) {
            if (platformAdminCount() > 0) {
                LOGGER.info(
                        "Development platform administrator bootstrap lost "
                                + "a concurrent create race and was skipped");
                return;
            }
            throw exception;
        }

        LOGGER.warn(
                "Created development-only platform administrator login={}; "
                        + "disable defaultPlatformAdminEnabled outside "
                        + "local development",
                properties.getLoginName());
    }

    private void requireDevelopmentMode() {
        String externalMode = environment.getProperty(
                "ecobin.external.mode", "fake");
        boolean productionProfile = environment.acceptsProfiles(
                Profiles.of("prod", "production"));
        if (productionProfile
                || !"fake".equals(externalMode.toLowerCase(Locale.ROOT))) {
            throw new IllegalStateException(
                    "development default platform administrator must be "
                            + "disabled for REAL mode and production profiles");
        }
    }

    private void validateConfiguration() {
        String loginName = properties.getLoginName();
        if (loginName == null
                || !LOGIN_NAME.matcher(loginName).matches()
                || !loginName.equals(
                        loginName.trim().toLowerCase(Locale.ROOT))) {
            throw invalidSetting("login-name");
        }
        String password = properties.getPassword();
        if (password == null
                || password.length() < 8
                || password.length() > 256) {
            throw invalidSetting("password");
        }
        String displayName = properties.getDisplayName();
        if (displayName == null
                || displayName.isBlank()
                || displayName.length() > 100) {
            throw invalidSetting("display-name");
        }
    }

    private long platformAdminCount() {
        Long count = jdbc.queryForObject(
                "SELECT COUNT(*) FROM iam_platform_admin",
                Long.class);
        if (count == null) {
            throw new IllegalStateException(
                    "platform administrator count is unavailable");
        }
        return count;
    }

    private static IllegalStateException invalidSetting(String field) {
        return new IllegalStateException(
                "invalid development default platform administrator "
                        + "setting: " + field);
    }
}
