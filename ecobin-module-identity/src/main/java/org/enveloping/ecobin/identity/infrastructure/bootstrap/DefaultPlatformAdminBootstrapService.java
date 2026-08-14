package org.enveloping.ecobin.identity.infrastructure.bootstrap;

import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.UUID;

@Service
public class DefaultPlatformAdminBootstrapService {

    static final String LOGIN_NAME = "enveloping";
    static final String DISPLAY_NAME = "默认平台管理员";

    private final JdbcTemplate jdbc;
    private final PasswordEncoder passwordEncoder;
    private final AuditPort auditPort;
    private final DefaultPlatformAdminProperties properties;

    public DefaultPlatformAdminBootstrapService(
            JdbcTemplate jdbc,
            PasswordEncoder passwordEncoder,
            AuditPort auditPort,
            DefaultPlatformAdminProperties properties) {
        this.jdbc = jdbc;
        this.passwordEncoder = passwordEncoder;
        this.auditPort = auditPort;
        this.properties = properties;
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public boolean ensureDefaultAdministrator() {
        long total = countAdministrators();
        long defaults = countDefaultAdministrators();
        if (total > 0) {
            requireConsistentExistingDirectory(total, defaults);
            return false;
        }
        if (defaults != 0) {
            throw inconsistentDirectory(total, defaults);
        }

        String password = properties.getPassword();
        if (password == null
                || password.length() < 8
                || password.length() > 256) {
            throw new IllegalStateException(
                    "empty platform administrator directory requires a "
                            + "valid out-of-repository bootstrap password");
        }

        UUID administratorUid = UUID.randomUUID();
        try {
            int inserted = jdbc.update("""
                            INSERT INTO iam_platform_admin (
                                platform_admin_uid, admin_kind, login_name,
                                password_hash, display_name, enabled,
                                failed_login_count, locked_until,
                                auth_version, password_changed_at, deleted_at,
                                lock_version, created_at, updated_at
                            ) VALUES (
                                ?, 'DEFAULT', ?, ?, ?, 1,
                                0, NULL, 0, CURRENT_TIMESTAMP(3), NULL,
                                0, CURRENT_TIMESTAMP(3), CURRENT_TIMESTAMP(3)
                            )
                            """,
                    administratorUid.toString(),
                    LOGIN_NAME,
                    passwordEncoder.encode(password),
                    DISPLAY_NAME);
            if (inserted != 1) {
                throw new IllegalStateException(
                        "default platform administrator was not created");
            }
        } catch (DuplicateKeyException concurrentCreate) {
            long concurrentTotal = countAdministrators();
            long concurrentDefaults = countDefaultAdministrators();
            requireConsistentExistingDirectory(
                    concurrentTotal, concurrentDefaults);
            return false;
        }

        Instant occurredAt = Instant.now();
        auditPort.append(new AuditEntry(
                UUID.randomUUID(),
                UUID.randomUUID(),
                UUID.randomUUID(),
                AuditScopeKind.PLATFORM,
                null,
                null,
                AuditActorKind.SYSTEM,
                null,
                null,
                null,
                "IDENTITY_BOOTSTRAP",
                "平台管理员引导程序",
                "identity.platform-admin.bootstrap-created",
                "platform-admin",
                administratorUid.toString(),
                "SYSTEM_TASK",
                "SUCCEEDED",
                null,
                null,
                "{\"adminKind\":\"DEFAULT\",\"bootstrapCreated\":true}",
                occurredAt));
        return true;
    }

    private long countAdministrators() {
        Long count = jdbc.queryForObject(
                "SELECT COUNT(*) FROM iam_platform_admin", Long.class);
        if (count == null) {
            throw new IllegalStateException(
                    "platform administrator count is unavailable");
        }
        return count;
    }

    private long countDefaultAdministrators() {
        Long count = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM iam_platform_admin
                        WHERE admin_kind = 'DEFAULT'
                          AND enabled = 1
                          AND deleted_at IS NULL
                        """,
                Long.class);
        if (count == null) {
            throw new IllegalStateException(
                    "default platform administrator count is unavailable");
        }
        return count;
    }

    private static void requireConsistentExistingDirectory(
            long total,
            long defaults) {
        if (total < 1 || defaults != 1) {
            throw inconsistentDirectory(total, defaults);
        }
    }

    private static IllegalStateException inconsistentDirectory(
            long total,
            long defaults) {
        return new IllegalStateException(
                "platform administrator directory is inconsistent: total="
                        + total + ", activeDefaults=" + defaults);
    }
}
