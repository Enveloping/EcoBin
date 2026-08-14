package org.enveloping.ecobin.identity.infrastructure.bootstrap;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

/** Creates the protected default administrator only for a truly empty table. */
@Component
@ConditionalOnProperty(
        prefix = "ecobin.identity.default-platform-admin",
        name = "enabled",
        havingValue = "true")
public final class DefaultPlatformAdminInitializer
        implements ApplicationRunner {

    private static final Logger LOGGER = LoggerFactory.getLogger(
            DefaultPlatformAdminInitializer.class);

    private final DefaultPlatformAdminBootstrapService bootstrapService;

    public DefaultPlatformAdminInitializer(
            DefaultPlatformAdminBootstrapService bootstrapService) {
        this.bootstrapService = bootstrapService;
    }

    @Override
    public void run(ApplicationArguments arguments) {
        if (bootstrapService.ensureDefaultAdministrator()) {
            LOGGER.warn(
                    "Created the protected default platform administrator "
                            + "login={}; change its password through account "
                            + "settings when operational policy requires it",
                    DefaultPlatformAdminBootstrapService.LOGIN_NAME);
            return;
        }
        LOGGER.info(
                "Default platform administrator bootstrap skipped because "
                        + "a consistent administrator directory already exists");
    }
}
