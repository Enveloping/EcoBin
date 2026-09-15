package org.enveloping.ecobin.bootstrap.database.epoch;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.InitializingBean;
import org.springframework.boot.health.contributor.Health;
import org.springframework.boot.health.contributor.HealthIndicator;
import org.springframework.core.env.Environment;
import org.springframework.core.env.Profiles;
import org.springframework.stereotype.Component;

/**
 * 在应用上下文完成启动前执行目标数据库纪元门禁，并把同一判定加入 readiness。
 */
@Component("dbEpochHealthIndicator")
public final class DatabaseEpochGuard
        implements InitializingBean, HealthIndicator {

    private static final Logger LOGGER =
            LoggerFactory.getLogger(DatabaseEpochGuard.class);

    private final DatabaseEpochVerifier verifier;
    private final DatabaseEpochGuardProperties properties;
    private final Environment environment;

    public DatabaseEpochGuard(
            DatabaseEpochVerifier verifier,
            DatabaseEpochGuardProperties properties,
            Environment environment) {
        this.verifier = verifier;
        this.properties = properties;
        this.environment = environment;
    }

    @Override
    public void afterPropertiesSet() {
        if (testBypassAllowed()) {
            LOGGER.warn(
                    "Target database epoch guard bypassed for in-memory boundary tests");
            return;
        }
        P0DatabaseEpochPolicy.Verification verification = verifier.verify();
        LOGGER.info(
                "Target database epoch accepted catalog={} principal={} minimumVersion=V{}",
                verification.catalog(),
                verification.principal(),
                verification.minimumVersion());
    }

    @Override
    public Health health() {
        if (testBypassAllowed()) {
            return Health.up()
                    .withDetail("mode", "in-memory-test-bypass")
                    .build();
        }
        try {
            P0DatabaseEpochPolicy.Verification verification = verifier.verify();
            return Health.up()
                    .withDetail("catalog", verification.catalog())
                    .withDetail("minimumVersion", verification.minimumVersion())
                    .withDetail("epoch", "P0_V1_TO_V84")
                    .build();
        } catch (DatabaseEpochException exception) {
            return Health.down()
                    .withDetail("reason", exception.getMessage())
                    .build();
        }
    }

    private boolean testBypassAllowed() {
        if (!properties.isTestBypass()) {
            return false;
        }
        if (!environment.acceptsProfiles(Profiles.of("test"))
                || !verifier.isEmbeddedH2()) {
            throw new DatabaseEpochException(
                    "epoch test bypass is only allowed for the test profile with in-memory H2");
        }
        return true;
    }
}
