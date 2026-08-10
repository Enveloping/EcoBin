package org.enveloping.ecobin.device.application.target;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

/** Reads the single platform policy injected into every device config. */
@Component
final class RuntimeSnapshotPolicyProvider {

    static final long DEFAULT_FALLBACK_INTERVAL_MS = 3_600_000L;
    static final long MINIMUM_FALLBACK_INTERVAL_MS = 600_000L;
    static final long FIXED_MISS_THRESHOLD = 3L;

    private final JdbcTemplate jdbc;

    RuntimeSnapshotPolicyProvider(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    Policy current() {
        return jdbc.queryForObject("""
                        SELECT policy_version, fallback_interval_ms
                        FROM dev_runtime_snapshot_policy
                        WHERE singleton_id = 1
                        """,
                (rs, ignored) -> new Policy(
                        rs.getLong("policy_version"),
                        rs.getLong("fallback_interval_ms")));
    }

    record Policy(long version, long fallbackIntervalMs) {
    }
}
